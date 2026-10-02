import pytest

from grab.models.schemas import BookingForm, DoctorPageTarget
from grab.services.booking import PageBookingStrategy
from grab.transactions.store import AttemptStore


@pytest.mark.parametrize("effect", ["weak-success", "rate-limit", "lost-response"])
async def test_real_locator_click_is_durable_and_never_repeated(
    chromium_page,
    tmp_path,
    effect,
):
    page = chromium_page
    await page.context.route(
        "**/*",
        lambda route: route.fulfill(
            content_type="text/html",
            body='<input name="schedule_id" value="slot"><input type="hidden" name="mid" value="member"><button id="submitbtn">预约</button>',
        ),
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
    effect,
):
    from tests.contracts.booking.scenarios import USERSCRIPT

    page = chromium_page
    source = USERSCRIPT.read_text(encoding="utf-8")
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


@pytest.mark.parametrize("mode", ["accept", "reject", "noninteractive", "manual"])
async def test_userscript_real_controller_enforces_new_consent(chromium_page, mode):
    from tests.contracts.booking.scenarios import USERSCRIPT

    page = chromium_page
    await page.context.route(
        "**/*",
        lambda route: route.fulfill(
            content_type="text/html",
            body="""<form id="suborder">
        <input name="schedule_id" value="slot"><input type="hidden" name="member_id" value="SYN_MEMBER">
        <button id="submitbtn" type="button">预约</button></form>""",
        ),
    )
    await page.goto(
        "https://synthetic.invalid/guahao/ystep1/uid-u/depid-d/schid-slot.html"
    )
    await page.evaluate("window.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__ = true;")
    await page.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
    prompts = []

    async def respond(dialog):
        prompts.append(dialog.message)
        if mode == "accept":
            await dialog.accept()
        else:
            await dialog.dismiss()

    page.on("dialog", respond)
    result = await page.evaluate(
        """async (mode) => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        h.writeSettings({booking:{autoSubmit:mode !== 'manual', autoReturnAfterSubmitFailure:true},target:{unitId:'u',depId:'d',doctorId:'doc'}, member:{memberId:'SYN_MEMBER'},pacing:{pageActionMs:[0,0]}});
        window.clicks = 0;
        document.querySelector('#submitbtn').onclick = () => { clicks++; };
        let id;
        if (mode === 'noninteractive') { id = 'booking:synthetic'; h.writeState({running:true,controllerId:id}); }
        else id = h.prepareManualControllerStart('booking');
        await h.runBookingPageController(id);
        return {clicks, outcome:h.readState().outcome, settings:h.readSettings(), journal:h.readJournal()};
    }""",
        mode,
    )
    assert result["clicks"] == (1 if mode == "accept" else 0)
    assert result["outcome"] == (
        "OUTCOME_UNKNOWN" if mode == "accept" else "AWAITING_MANUAL_CONFIRMATION"
    )
    assert len(prompts) == (1 if mode in {"accept", "reject"} else 0)
    if prompts:
        assert (
            "SYN_MEMBER" in prompts[0] and "未知" in prompts[0] and "禁止" in prompts[0]
        )
    assert "SYN_MEMBER" not in str(result["journal"])
    if mode == "reject":
        assert result["settings"]["booking"]["submitMode"] == "manual_confirm"


async def test_userscript_rechecks_revoke_after_async_prepare(chromium_page):
    from tests.contracts.booking.scenarios import USERSCRIPT

    page = chromium_page
    await page.context.route(
        "**/*",
        lambda route: route.fulfill(
            content_type="text/html",
            body='<input name="schedule_id" value="slot"><input type="hidden" name="member_id" value="member"><button id="submitbtn">预约</button>',
        ),
    )
    await page.goto(
        "https://synthetic.invalid/guahao/ystep1/uid-u/depid-d/schid-slot.html"
    )
    await page.evaluate("window.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__ = true;")
    await page.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
    page.on("dialog", lambda dialog: dialog.accept())
    await page.evaluate("""() => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        h.writeSettings({target:{unitId:'u',depId:'d',doctorId:'doc'}, member:{memberId:'member'},pacing:{pageActionMs:[1000,1000]}});
        window.clicks = 0;
        document.querySelector('#submitbtn').onclick = () => { clicks++; };
        window.controllerFinished = false;
        const id = h.prepareManualControllerStart('booking');
        h.runBookingPageController(id).then(() => {controllerFinished = true;});
    }""")
    await page.wait_for_function(
        "__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.readJournal().consents.length === 1"
    )
    # A different page's revocation changes the durable store, without changing
    # this page's controller or sessionStorage.
    other = await page.context.new_page()
    await other.goto("https://synthetic.invalid/revoke")
    await other.evaluate(
        """(key) => {
        const state = JSON.parse(localStorage.getItem(key));
        state.consents = [];
        localStorage.setItem(key, JSON.stringify(state));
    }""",
        "grab160.submissionJournal.v1",
    )
    await page.wait_for_function("controllerFinished")
    result = await page.evaluate(
        """() => ({clicks, state:__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.readState().outcome, pending:__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.submissionBlocked()})"""
    )
    assert result == {
        "clicks": 0,
        "state": "AWAITING_MANUAL_CONFIRMATION",
        "pending": False,
    }


@pytest.mark.parametrize("changed", ["member", "target", "disease"])
async def test_userscript_rechecks_changed_settings_from_another_tab(
    chromium_page, changed
):
    from tests.contracts.booking.scenarios import USERSCRIPT

    page = chromium_page
    await page.context.route(
        "**/*",
        lambda route: route.fulfill(
            content_type="text/html",
            body='<input name="schedule_id" value="slot"><input type="hidden" name="member_id" value="member"><textarea name="disease_input">synthetic-existing</textarea><button id="submitbtn">预约</button>',
        ),
    )
    await page.goto(
        "https://synthetic.invalid/guahao/ystep1/uid-u/depid-d/schid-slot.html"
    )
    await page.evaluate("window.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__ = true;")
    await page.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
    page.on("dialog", lambda dialog: dialog.accept())
    await page.evaluate("""() => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        h.writeSettings({target:{unitId:'u',depId:'d',doctorId:'doc'}, member:{memberId:'member'},pacing:{pageActionMs:[1000,1000]}});
        window.clicks = 0;
        document.querySelector('#submitbtn').onclick = () => { clicks++; };
        window.controllerFinished = false;
        h.runBookingPageController(h.prepareManualControllerStart('booking')).then(() => {controllerFinished = true;});
    }""")
    await page.wait_for_function(
        "__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.readJournal().consents.length === 1"
    )
    other = await page.context.new_page()
    await other.goto("https://synthetic.invalid/settings")
    await other.evaluate(
        """changed => {
        const key = 'grab160.doctorPagePoller.settings.v2';
        const settings = JSON.parse(localStorage.getItem(key));
        if (changed === 'member') settings.member.memberId = 'synthetic-other-member';
        if (changed === 'target') settings.target.doctorId = 'synthetic-other-doctor';
        if (changed === 'disease') settings.booking.diseaseDescription = 'synthetic-new-description';
        localStorage.setItem(key, JSON.stringify(settings));
    }""",
        changed,
    )
    await page.wait_for_function("controllerFinished")
    assert await page.evaluate(
        """() => ({clicks, state:__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.readState().outcome, pending:__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.submissionBlocked()})"""
    ) == dict(clicks=0, state="AWAITING_MANUAL_CONFIRMATION", pending=False)


@pytest.mark.parametrize("mismatch", ["unitId", "depId", "scheduleId"])
async def test_userscript_rejects_booking_page_that_differs_from_navigation_handoff(
    chromium_page, mismatch
):
    from tests.contracts.booking.scenarios import USERSCRIPT

    page = chromium_page
    await page.context.route(
        "**/*",
        lambda route: route.fulfill(
            content_type="text/html",
            body='<input name="schedule_id" value="slot"><input type="hidden" name="member_id" value="member"><button id="submitbtn">预约</button>',
        ),
    )
    await page.goto(
        "https://synthetic.invalid/guahao/ystep1/uid-u/depid-d/schid-slot.html"
    )
    await page.evaluate("window.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__ = true;")
    await page.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
    prompts = []

    async def authorize(dialog):
        prompts.append(dialog.message)
        await dialog.accept()

    page.on("dialog", authorize)
    result = await page.evaluate(
        """async mismatch => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        h.writeSettings({target:{unitId:'u',depId:'d',doctorId:'doc'},member:{memberId:'member'},
            pacing:{pageActionMs:[0,0],bookingSubmitSettleMs:[0,0]}});
        window.clicks = 0;
        document.querySelector('#submitbtn').onclick = () => { clicks++; };
        const pendingBooking = {unitId:'u',depId:'d',doctorId:'doc',scheduleId:'slot'};
        pendingBooking[mismatch] = 'synthetic-other';
        h.writeState({running:true,interactiveConsent:true,pendingBooking});
        await h.runBookingPageController(h.claimPageController('booking'));
        return {clicks,outcome:h.readState().outcome,pending:h.submissionBlocked()};
    }""",
        mismatch,
    )
    assert result == dict(
        clicks=0, outcome="AWAITING_MANUAL_CONFIRMATION", pending=False
    )
    assert prompts == []


async def test_userscript_pending_survives_real_browser_restart(tmp_path):
    from playwright.async_api import async_playwright

    from tests.contracts.booking.scenarios import USERSCRIPT

    source = USERSCRIPT.read_text(encoding="utf-8")
    profile = tmp_path / "synthetic-browser-profile"
    async with async_playwright() as pw:
        for restarted in (False, True):
            context = await pw.chromium.launch_persistent_context(
                str(profile), headless=True, service_workers="block"
            )
            try:
                await context.route(
                    "**/*",
                    lambda route: route.fulfill(
                        content_type="text/html",
                        body='<button id="submitbtn">预约</button>',
                    ),
                )
                page = context.pages[0]
                await page.goto("https://synthetic.invalid/booking")
                await page.evaluate(
                    "window.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__ = true;"
                )
                await page.evaluate(source)
                result = await page.evaluate("""async () => {
                    const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
                    document.querySelector('#submitbtn').onclick = () => {
                        localStorage.setItem('clicks', String(Number(localStorage.getItem('clicks') || 0)+1));
                    };
                    const state = await h.submitTransaction(h.findSubmitControl(), {scheduleId:'slot'}, {memberId:'member'}, {unitId:'u',depId:'d',doctorId:'doc'}, true);
                    return {state, clicks:localStorage.getItem('clicks')};
                }""")
                assert result == {"state": "OUTCOME_UNKNOWN", "clicks": "1"}
                if restarted:
                    assert await page.evaluate(
                        "__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.submissionBlocked()"
                    )
            finally:
                await context.close()
