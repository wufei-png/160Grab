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
    if "native_required_valid" in case:
        assert (
            await page.locator("#suborder").evaluate("node => node.checkValidity()")
            == case["native_required_valid"]
        )


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


async def load_userscript(page):
    from tests.contracts.booking.scenarios import USERSCRIPT

    await page.evaluate("window.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__ = true;")
    await page.evaluate(USERSCRIPT.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", PREPARATION, ids=lambda c: c["id"])
async def test_js_dom_adapter_matches_python_and_shared_contract(chromium_page, case):
    from grab.booking.page import read_snapshot

    page = chromium_page
    await load_case(page, case)
    await load_userscript(page)
    python_snapshot = await read_snapshot(page)
    result = await page.evaluate(
        """async (c) => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        const snapshot = h.readBookingSnapshot();
        const decision = h.decideBookingPreparation(snapshot,c.selection,c.values);
        const form = {scheduleId:c.selection.schedule_id,appointmentValue:c.selection.appointment_value,expectedDate:c.selection.date};
        const member = {memberId:c.selection.member_id};
        const address = Object.fromEntries(Object.entries(c.values).filter(([k]) => k.startsWith('address.')).map(([k,v]) => [k.slice(8),v]));
        const booking = {diseaseDescription:c.values.disease_input,clinicCard:c.values.card};
        const preparation = await h.prepareBookingFormForSubmit(form,member,address,booking);
        const outcome = await h.submitTransaction(h.findSubmitControl(),form,member,{unitId:'synthetic-unit',depId:'synthetic-dept',doctorId:'synthetic-doctor'},true,null,
            () => h.readBookingFormReadiness(form,member,address,booking).ok);
        return {snapshot,decision,preparation,outcome,submitClicks:window.submitClicks,selectionClicks:window.selectionClicks};
    }""",
        case,
    )
    assert result["snapshot"] == python_snapshot
    expected = {k: v for k, v in case["expected"].items() if k != "submit_clicks"}
    assert {k: result["decision"][k] for k in expected} == expected
    assert result["submitClicks"] == case["expected"]["submit_clicks"]
    if not result["decision"]["can_prepare"]:
        assert result["selectionClicks"] == 0
    assert result["outcome"] == (
        "OUTCOME_UNKNOWN"
        if case["expected"]["submit_clicks"]
        else "AWAITING_MANUAL_CONFIRMATION"
    )


async def test_js_waits_for_delayed_card_and_button(chromium_page):
    page = chromium_page
    case = next(c for c in PREPARATION if c["id"] == "card-missing")
    await load_case(page, case)
    await load_userscript(page)
    result = await page.evaluate("""async () => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        document.querySelector('#submitbtn').disabled = true;
        setTimeout(() => {document.querySelector('#hismemid').value = 'synthetic-card';document.querySelector('#submitbtn').disabled = false;},100);
        const form = {scheduleId:'synthetic-slot',appointmentValue:'synthetic-time',expectedDate:'2030-01-02'};
        const member = {memberId:'synthetic-member'};
        const result = await h.prepareBookingFormForSubmit(form,member,{},{});
        return {ok:result.ok,attempt:result.attempt,submitClicks:window.submitClicks};
    }""")
    assert result["ok"] and 1 < result["attempt"] <= 3
    assert result["submitClicks"] == 0


async def test_js_native_identity_check_remains_untouched(chromium_page):
    case = next(c for c in PREPARATION if c["id"] == "address-absent")
    page = chromium_page
    await load_case(page, case)
    await page.evaluate("""() => {
        window.identityCalls = 0;
        window.jQuery = {ajax:()=> {window.identityCalls++; return '';}};
        window.originalAjax = window.jQuery.ajax;
        document.querySelector('input[type="radio"]').onclick = () => window.jQuery.ajax({url:'/guahao/checkIdInfo.html',dataType:'json'});
    }""")
    await load_userscript(page)
    result = await page.evaluate("""async () => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        await h.prepareBookingFormForSubmit({scheduleId:'synthetic-slot',appointmentValue:'synthetic-time'}, {memberId:'synthetic-member'}, {}, {});
        return {calls:window.identityCalls,untouched:window.jQuery.ajax === window.originalAjax,response:window.jQuery.ajax({url:'/guahao/checkIdInfo.html'})};
    }""")
    assert result == dict(calls=1, untouched=True, response="")


@pytest.mark.parametrize("path", ["python", "js"])
async def test_both_adapters_wait_for_async_address_cascade(
    chromium_page, tmp_path, path
):
    case = dict(next(c for c in PREPARATION if c["id"] == "address-absent"))
    fields = """<select id="useraddress_province"><option value="0">Choose</option><option value="synthetic-province">Synthetic province</option></select>
        <select id="useraddress_city"><option value="0">Choose</option></select>
        <select id="useraddress_area"><option value="0">Choose</option></select><input name="address">"""
    case["html"] = case["html"].replace("</form>", fields + "</form>")
    case["values"] = {
        "address.province": "synthetic-province",
        "address.city": "synthetic-city",
        "address.area": "synthetic-area",
        "address.detail": "synthetic-detail",
    }
    page = chromium_page
    await load_case(page, case)
    await page.evaluate("""() => {
        const city = document.querySelector('#useraddress_city'), area = document.querySelector('#useraddress_area');
        document.querySelector('#useraddress_province').onchange = () => setTimeout(() => {city.innerHTML += '<option value="synthetic-city">Synthetic city</option>';},50);
        city.onchange = () => setTimeout(() => {area.innerHTML += '<option value="synthetic-area">Synthetic area</option>';},50);
    }""")
    if path == "python":
        strategy, form = strategy_for(page, case, tmp_path), form_for(case)
        await strategy.fill_booking_form(form)
        assert form.is_valid
        assert (await strategy.submit_open_form(form)).state == "OUTCOME_UNKNOWN"
    else:
        await load_userscript(page)
        result = await page.evaluate("""async () => {
            const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
            const form = {scheduleId:'synthetic-slot',appointmentValue:'synthetic-time',expectedDate:'2030-01-02'},member = {memberId:'synthetic-member'};
            const address = {province:'synthetic-province',city:'synthetic-city',area:'synthetic-area',detail:'synthetic-detail'};
            const preparation = await h.prepareBookingFormForSubmit(form,member,address,{});
            const outcome = await h.submitTransaction(h.findSubmitControl(),form,member,{unitId:'synthetic-unit',depId:'synthetic-dept',doctorId:'synthetic-doctor'},true,null,()=>h.readBookingFormReadiness(form,member,address,{}).ok);
            return {ok:preparation.ok,outcome};
        }""")
        assert result == dict(ok=True, outcome="OUTCOME_UNKNOWN")
    assert await page.locator("#useraddress_area").input_value() == "synthetic-area"
    assert await page.evaluate("window.submitClicks") == 1


async def test_js_controller_rechecks_form_after_settle_and_hashing(chromium_page):
    case = dict(next(c for c in PREPARATION if c["id"] == "explicit-values"))
    page = chromium_page
    await load_case(page, case)
    await load_userscript(page)
    page.on("dialog", lambda dialog: dialog.accept())
    result = await page.evaluate("""async () => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        history.replaceState({},'', '/guahao/ystep1/uid-synthetic-unit/depid-synthetic-dept/schid-synthetic-slot.html');
        h.writeSettings({settingsVersion:4, member:{memberId:'synthetic-member'},target:{unitId:'synthetic-unit',depId:'synthetic-dept',doctorId:'synthetic-doctor'},
            booking:{clinicCard:'synthetic-card',diseaseDescription:'synthetic-config'},address:{detail:'synthetic-address'},pacing:{pageActionMs:[100,100]}});
        await h.ensureSubmissionConsent({unitId:'synthetic-unit',depId:'synthetic-dept',doctorId:'synthetic-doctor'},{memberId:'synthetic-member'},{interactive:true});
        h.writeState({running:true}); const id = h.claimPageController('booking');
        setTimeout(() => {document.querySelector('[name="disease_input"]').value = 'synthetic-late-conflict';},50);
        await h.runBookingPageController(id);
        return {submitClicks:window.submitClicks,pending:h.submissionBlocked(),outcome:h.readState().outcome};
    }""")
    assert result == dict(
        submitClicks=0, pending=False, outcome="AWAITING_MANUAL_CONFIRMATION"
    )


async def test_python_wrong_schedule_stops_before_any_selection(
    chromium_page, tmp_path
):
    case = next(c for c in PREPARATION if c["id"] == "address-absent")
    page = chromium_page
    await load_case(page, case)
    strategy = strategy_for(page, case, tmp_path)
    form = await strategy.open_booking_form("synthetic-wrong-slot")
    assert not form.is_valid
    assert form.blockers == ["schedule.mismatch"]
    assert (
        await strategy.submit_open_form(form)
    ).state == "AWAITING_MANUAL_CONFIRMATION"
    assert await page.evaluate("window.selectionClicks") == 0
    assert await page.evaluate("window.submitClicks") == 0


@pytest.mark.parametrize("path", ["python", "js"])
@pytest.mark.parametrize("invalid", ["card-pattern", "disease-custom", "late-custom"])
async def test_native_invalid_known_fields_never_enter_submission(
    chromium_page, tmp_path, path, invalid
):
    case = next(c for c in PREPARATION if c["id"] == "existing-values")
    page = chromium_page
    await load_case(page, case)
    strategy, form = strategy_for(page, case, tmp_path), form_for(case)
    if path == "js":
        await load_userscript(page)
    # A late invalidation must also be caught by the final read-only gate.
    if invalid == "late-custom":
        if path == "python":
            await strategy.fill_booking_form(form)
            assert form.is_valid
        else:
            assert await page.evaluate("""async () => (await __GRAB160_DOCTOR_POLLER_TEST_HOOKS__.prepareBookingFormForSubmit(
                {scheduleId:'synthetic-slot',appointmentValue:'synthetic-time',expectedDate:'2030-01-02'},
                {memberId:'synthetic-member'},{},{})).ok""")
    await page.evaluate(
        """invalid => {
        if (invalid === 'card-pattern') document.querySelector('#hismemid').pattern = '[0-9]+';
        else document.querySelector('[name="disease_input"]').setCustomValidity('Synthetic validation blocker');
    }""",
        invalid,
    )
    assert not await page.locator("#suborder").evaluate("node => node.checkValidity()")
    if path == "python":
        if invalid != "late-custom":
            await strategy.fill_booking_form(form)
        result = await strategy.submit_open_form(form)
        assert result.state == "AWAITING_MANUAL_CONFIRMATION"
        assert not strategy.attempt_store.pending()
    else:
        from grab.booking.page import read_snapshot

        assert await page.evaluate(
            "__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.readBookingSnapshot()"
        ) == await read_snapshot(page)
        result = await page.evaluate(
            """async invalid => {
            const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
            const form = {scheduleId:'synthetic-slot',appointmentValue:'synthetic-time',expectedDate:'2030-01-02'};
            const member = {memberId:'synthetic-member'};
            const preparation = invalid === 'late-custom' ? {ok:true} : await h.prepareBookingFormForSubmit(form,member,{},{});
            const outcome = await h.submitTransaction(h.findSubmitControl(),form,member,
                {unitId:'synthetic-unit',depId:'synthetic-dept',doctorId:'synthetic-doctor'},true,null,
                () => h.readBookingFormReadiness(form,member,{},{}).ok);
            return {ok:preparation.ok,outcome,pending:h.submissionBlocked()};
        }""",
            invalid,
        )
        assert result == dict(
            ok=invalid == "late-custom",
            outcome="AWAITING_MANUAL_CONFIRMATION",
            pending=False,
        )
    assert await page.evaluate("window.submitClicks") == 0
