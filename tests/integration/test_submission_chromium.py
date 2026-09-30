import pytest

from grab.models.schemas import BookingForm, DoctorPageTarget
from grab.services.booking import PageBookingStrategy
from grab.transactions.store import AttemptStore
from tests.integration.test_booking_chromium import chromium_page  # noqa: F401, F811


@pytest.mark.parametrize("effect", ["weak-success", "rate-limit", "lost-response"])
async def test_real_locator_click_is_durable_and_never_repeated(
    chromium_page,
    tmp_path,
    effect,  # noqa: F811
):
    page = chromium_page
    await page.context.route(
        "**/*", lambda route: route.fulfill(body='<button id="submitbtn">预约</button>')
    )
    await page.goto("https://synthetic.invalid/booking")
    await page.evaluate(
        """(effect) => {
        window.clickCount = 0;
        document.querySelector('#submitbtn').onclick = () => {
            window.clickCount++;
            document.body.innerHTML = effect === 'rate-limit' ? '访问次数过多' : '页面已变化';
        };
    }""",
        effect,
    )
    store = AttemptStore(tmp_path)
    obj = PageBookingStrategy(page, attempt_store=store, authorization=lambda *_: True)
    obj.prepare(
        DoctorPageTarget(unit_id="u", dept_id="d", doctor_id="doc", source_url=""),
        "member",
    )
    result = await obj.submit_open_form(
        BookingForm(member_id="member", schedule_id="slot")
    )
    assert result.state == "OUTCOME_UNKNOWN"
    assert await page.evaluate("window.clickCount") == 1
    await page.reload()
    restarted = PageBookingStrategy(
        page, attempt_store=AttemptStore(tmp_path), authorization=lambda *_: True
    )
    restarted.prepare(obj.target, "member")
    assert (await restarted.submit_with_retry("other")).state == "OUTCOME_UNKNOWN"
    assert store.pending()


@pytest.mark.parametrize(
    "effect", ["throw", "navigation", "weak-success", "rate-limit"]
)
async def test_userscript_reload_and_restart_never_clear_unknown(
    chromium_page,
    effect,  # noqa: F811
):
    from tests.contracts.booking.scenarios import USERSCRIPT

    page = chromium_page
    source = USERSCRIPT.read_text()
    await page.context.route(
        "**/*", lambda route: route.fulfill(body='<button id="submitbtn">预约</button>')
    )
    await page.add_init_script(
        "window.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__ = true;"
    )
    await page.goto("https://synthetic.invalid/booking")
    await page.evaluate(source)
    await page.evaluate(
        """(effect) => {
        document.querySelector('#submitbtn').onclick = () => {
            localStorage.setItem('syntheticClicks', String(Number(localStorage.getItem('syntheticClicks') || 0)+1));
            if (effect === 'throw') throw new Error('synthetic click race');
            if (effect === 'navigation') location.href = '/weak-success';
            else document.body.innerHTML = effect === 'rate-limit' ? '访问次数过多' : '页面已变化';
        };
    }""",
        effect,
    )
    # Navigation may destroy this evaluation. The durable SUBMITTING is already written.
    try:
        await page.evaluate("""async () => {
            const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
            return await h.submitTransaction(h.findSubmitControl(), {scheduleId:'slot', appointmentValue:'time'}, {memberId:'member'}, {unitId:'u',depId:'d',doctorId:'doc'}, true);
        }""")
    except Exception:
        assert effect == "navigation"
    if effect == "navigation":
        await page.wait_for_url("**/weak-success")
    await page.wait_for_load_state()
    await page.reload()
    await page.evaluate(source)
    result = await page.evaluate("""async () => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        h.stopRun(); h.resetRuntimeState(); h.startRun();
        const outcome = await h.submitTransaction(h.findSubmitControl(), {scheduleId:'other'}, {memberId:'other'}, {}, true);
        return {outcome, blocked:h.submissionBlocked(), clicks:localStorage.getItem('syntheticClicks'), state:h.readState().outcome};
    }""")
    assert result == {
        "outcome": "OUTCOME_UNKNOWN",
        "blocked": True,
        "clicks": "1",
        "state": "OUTCOME_UNKNOWN",
    }
