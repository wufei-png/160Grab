import pytest

from grab.booking.page import read_decision
from grab.models.schemas import BookingForm, DoctorPageTarget, GrabConfig
from grab.services.booking import BookingService, PageBookingStrategy
from grab.transactions.store import AttemptStore
from tests.contracts.booking.test_form_decisions import PREPARATION


def config_for(case):
    values = case["values"]
    return GrabConfig(
        booking={
            "clinic_card": values.get("card"),
            "disease_description": values.get("disease_input"),
            "address": {
                k.removeprefix("address."): v
                for k, v in values.items()
                if k.startswith("address.")
            },
        },
        page_action_sleep_time="0",
    )


async def load_case(page, case):
    await page.context.route(
        "**/*", lambda route: route.fulfill(body=case["html"], content_type="text/html")
    )
    await page.add_init_script("""(() => {
        window.submitClicks = 0; window.selectionClicks = 0;
        document.addEventListener('click', e => {
            if(e.target.matches('#submitbtn, #submit_booking')) window.submitClicks++;
            if(e.target.matches('#delts li[val]')) {
                window.selectionClicks++;
                document.querySelectorAll('#delts li').forEach(n => n.classList.remove('selected'));
                e.target.classList.add('selected');
            }
            if(e.target.matches('input[type="radio"]')) window.selectionClicks++;
        });
    })();""")
    await page.goto("https://synthetic.invalid/booking")


def strategy_for(page, case, tmp_path):
    strategy = PageBookingStrategy(
        page,
        config_for(case),
        attempt_store=AttemptStore(tmp_path),
        authorization=lambda *_: True,
    )
    strategy.prepare(
        DoctorPageTarget(
            unit_id="synthetic-unit",
            dept_id="synthetic-dept",
            doctor_id="synthetic-doctor",
            source_url="",
        ),
        case["selection"]["member_id"],
    )
    return strategy


def form_for(case):
    s = case["selection"]
    return BookingForm(
        member_id=s["member_id"],
        schedule_id=s["schedule_id"],
        appointment_value=s["appointment_value"],
        schedule_date=s["date"],
    )


@pytest.mark.parametrize("case", PREPARATION, ids=lambda c: c["id"])
async def test_python_preparation_and_submit_contract(chromium_page, tmp_path, case):
    page = chromium_page
    await load_case(page, case)
    strategy, form = strategy_for(page, case, tmp_path), form_for(case)
    _, decision = await read_decision(page, form, strategy.config)
    expected = {k: v for k, v in case["expected"].items() if k != "submit_clicks"}
    assert {k: decision[k] for k in expected} == expected
    await strategy.fill_booking_form(form)
    result = await strategy.submit_open_form(form)
    assert (
        await page.evaluate("window.submitClicks") == case["expected"]["submit_clicks"]
    )
    assert result.state == (
        "OUTCOME_UNKNOWN"
        if case["expected"]["submit_clicks"]
        else "AWAITING_MANUAL_CONFIRMATION"
    )
    if not decision["can_prepare"]:
        assert await page.evaluate("window.selectionClicks") == 0


async def test_python_waits_for_delayed_card_and_disabled_button(
    chromium_page, tmp_path
):
    case = next(c for c in PREPARATION if c["id"] == "card-missing")
    page = chromium_page
    await load_case(page, case)
    await page.evaluate("""() => {
        document.querySelector('#submitbtn').disabled = true;
        setTimeout(() => {
            document.querySelector('#hismemid').value = 'synthetic-card';
            document.querySelector('#submitbtn').disabled = false;
        }, 100);
    }""")
    strategy, form = strategy_for(page, case, tmp_path), form_for(case)
    await strategy.fill_booking_form(form)
    assert form.is_valid
    await strategy.submit_open_form(form)
    assert await page.evaluate("window.submitClicks") == 1


async def test_python_final_recheck_catches_late_conflict(chromium_page, tmp_path):
    case = next(c for c in PREPARATION if c["id"] == "explicit-values")
    page = chromium_page
    await load_case(page, case)
    strategy, form = strategy_for(page, case, tmp_path), form_for(case)
    await strategy.fill_booking_form(form)
    await page.locator('[name="disease_input"]').fill("synthetic-conflict")
    assert (
        await strategy.submit_open_form(form)
    ).state == "AWAITING_MANUAL_CONFIRMATION"
    assert await page.evaluate("window.submitClicks") == 0
    assert not strategy.attempt_store.pending()


async def test_required_blocker_stops_service_before_another_slot(
    chromium_page, tmp_path
):
    from grab.models.schemas import Slot

    case = next(c for c in PREPARATION if c["id"] == "card-missing")
    page = chromium_page
    await load_case(page, case)
    strategy = strategy_for(page, case, tmp_path)
    opened = []
    native_open = strategy.open_booking_form

    async def track(slot_id):
        opened.append(slot_id)
        return await native_open(slot_id)

    strategy.open_booking_form = track
    result = await BookingService(strategy).try_book_first_available(
        [Slot(schedule_id="synthetic-slot"), Slot(schedule_id="synthetic-other")]
    )
    assert result.state == "AWAITING_MANUAL_CONFIRMATION"
    assert opened == ["synthetic-slot"]
    assert await page.evaluate("window.submitClicks") == 0
