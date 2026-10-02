import asyncio

import pytest

from tests.contracts.booking.scenarios import USERSCRIPT

ORIGIN = "https://www.91160.com"
DOCTOR = ORIGIN + "/doctors/index/unit_id-u/dep_id-d/docid-doc.html"
BOOKING = ORIGIN + "/guahao/ystep1/uid-u/depid-d/schid-slot.html"
HTML = '<input name="schedule_id" value="slot"><input type="hidden" name="member_id" value="member"><button id="submitbtn" type="button">预约</button>'
HOOKS = "__GRAB160_DOCTOR_POLLER_TEST_HOOKS__"


async def pages_for(page, url=DOCTOR):
    context = page.context
    await context.route(
        "**/*", lambda route: route.fulfill(content_type="text/html", body=HTML)
    )
    await context.add_init_script(
        "window.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__ = true;"
    )
    other = await context.new_page()
    for p in (page, other):
        await p.goto(url)
        await p.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
        await p.evaluate("""() => {
            const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
            h.writeSettings({member:{memberId:'member'},target:{unitId:'u',depId:'d',doctorId:'doc'},pacing:{pageActionMs:[0,0],pollMs:[5000,5000]}});
            window._user_key = 'synthetic';
            window.polls = 0;
            window.jQuery = {ajax(options) { polls++; window.pollRespond = options.success; }};
            window.finished = false;
            document.querySelector('#submitbtn').onclick = () => localStorage.setItem('clicks', String(Number(localStorage.getItem('clicks') || 0)+1));
        }""")
    return page, other


START_DOCTOR = """() => {
    const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
    const id = h.prepareManualControllerStart('doctor');
    h.runDoctorPageController(id).then(() => {window.finished = true;});
}"""


async def test_two_pages_only_one_real_poll_controller_and_close_takeover(
    chromium_page,
):
    first, second = await pages_for(chromium_page)
    await asyncio.gather(first.evaluate(START_DOCTOR), second.evaluate(START_DOCTOR))
    await first.wait_for_function("polls === 1 || finished")
    await second.wait_for_function("polls === 1 || finished")
    counts = [await p.evaluate("polls") for p in (first, second)]
    assert sorted(counts) == [0, 1]
    leader, follower = (first, second) if counts[0] else (second, first)
    assert await follower.evaluate(f"{HOOKS}.readState().running") is False
    assert (
        await follower.evaluate(f"{HOOKS}.readState().summary.message")
        == "Automatic operation paused; leader unavailable. Continue manually."
    )
    await leader.close()  # Browser automatically releases the document's Web Lock.
    await follower.evaluate(START_DOCTOR)
    await follower.wait_for_function("polls === 1")
    assert await follower.evaluate(f"{HOOKS}.ownsBrowserLeader()") is True
    await follower.evaluate(f"{HOOKS}.stopRun()")


async def test_two_pages_only_one_submit_and_pending_blocks_takeover(chromium_page):
    pages = await pages_for(chromium_page, BOOKING)
    expression = """async () => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        return await h.submitTransaction(h.findSubmitControl(), {scheduleId:'slot'}, {memberId:'member'}, {unitId:'u',depId:'d',doctorId:'doc'}, true);
    }"""
    # The production booking controller already owns the leader before submit.
    # Establish that precondition before the follower races this transaction;
    # simultaneous startup/owner loss are covered by the other coordination tests.
    await pages[0].evaluate("""() => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        window.submitOutcome = null;
        void h.withBrowserLeader(async () => {
            await new Promise(resolve => {window.releaseSubmit = resolve;});
            return await h.submitTransaction(h.findSubmitControl(), {scheduleId:'slot'}, {memberId:'member'}, {unitId:'u',depId:'d',doctorId:'doc'}, true);
        }).then(outcome => {window.submitOutcome = outcome;});
    }""")
    await pages[0].wait_for_function("typeof releaseSubmit === 'function'")
    assert await pages[0].evaluate(f"{HOOKS}.ownsBrowserLeader()") is True
    _, follower_outcome = await asyncio.gather(
        pages[0].evaluate("releaseSubmit()"), pages[1].evaluate(expression)
    )
    await pages[0].wait_for_function("submitOutcome !== null")
    assert await pages[0].evaluate("submitOutcome") == "OUTCOME_UNKNOWN"
    assert follower_outcome in {"OUTCOME_UNKNOWN", "AWAITING_MANUAL_CONFIRMATION"}
    clicks = await pages[0].evaluate("localStorage.getItem('clicks')")
    assert clicks == "1", {
        "follower_outcome": follower_outcome,
        "pages": [
            await p.evaluate(f"""() => ({{
                visibility:document.visibilityState,
                states:{HOOKS}.readJournal().attempts.map(r => r.state),
                summary:{HOOKS}.readState().summary?.message
            }})""")
            for p in pages
        ],
    }
    await pages[0].close()
    follower = pages[1]
    await follower.reload()
    await follower.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
    assert await follower.evaluate(expression) == "OUTCOME_UNKNOWN"
    assert await follower.evaluate("localStorage.getItem('clicks')") == "1"


@pytest.mark.parametrize("loss", ["ttl", "hidden", "pagehide"])
async def test_loss_fences_awaiting_controller_and_releases_for_takeover(
    chromium_page, loss
):
    first, second = await pages_for(chromium_page)
    if loss == "ttl":
        await first.clock.install()
    await first.evaluate(START_DOCTOR)
    await first.wait_for_function("polls === 1")
    if loss == "ttl":
        # Fast-forward simulates a suspended callback; it cannot renew an expired owner.
        await first.clock.fast_forward(31000)
    elif loss == "hidden":
        await first.evaluate(
            "Object.defineProperty(document,'visibilityState',{configurable:true,value:'hidden'}); document.dispatchEvent(new Event('visibilitychange'));"
        )
    else:
        await first.evaluate(
            "window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}));"
        )
    await first.wait_for_function("finished")
    assert await first.evaluate(f"{HOOKS}.ownsBrowserLeader()") is False
    await second.evaluate(START_DOCTOR)
    await second.wait_for_function("polls === 1")
    # Resume the old async request after a new owner exists. No next request or navigation.
    await first.evaluate("pollRespond({result_code:1,data:{schedules:[]}})")
    assert await first.evaluate("polls") == 1
    assert (
        await first.evaluate("location.pathname")
        == "/doctors/index/unit_id-u/dep_id-d/docid-doc.html"
    )
    await second.evaluate(f"{HOOKS}.stopRun()")


async def test_doctor_to_ystep1_navigation_acquires_one_new_controller(chromium_page):
    first, other = await pages_for(chromium_page)
    await first.evaluate(START_DOCTOR)
    await first.wait_for_function("polls === 1")
    await other.evaluate(START_DOCTOR)
    await other.wait_for_function("finished")
    assert await other.evaluate("polls") == 0
    await first.evaluate(
        "pollRespond({result_code:1,data:{schedules:[{schedule_id:'slot',doctor_id:'doc',status:'available',weekday:3,day_period:'am'}]}})"
    )
    await first.wait_for_url(BOOKING)
    await first.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
    first.on("dialog", lambda dialog: dialog.accept())
    result = await first.evaluate("""async () => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        document.querySelector('#submitbtn').onclick = () => localStorage.setItem('clicks',String(Number(localStorage.getItem('clicks') || 0)+1));
        const id = h.claimPageController('booking');
        const duplicate = h.claimPageController('booking');
        await h.runBookingPageController(id);
        return {duplicate,clicks:localStorage.getItem('clicks'),state:h.readState().outcome};
    }""")
    assert result == {"duplicate": None, "clicks": "1", "state": "OUTCOME_UNKNOWN"}
    await other.evaluate(START_DOCTOR)
    assert await other.evaluate("polls") == 0


async def test_unavailable_web_locks_no_poll_no_submit(chromium_page):
    first, _ = await pages_for(chromium_page, BOOKING)
    await first.evaluate("Object.defineProperty(navigator,'locks',{value:undefined});")
    result = await first.evaluate("""async () => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        const id = h.prepareManualControllerStart('booking');
        await h.runBookingPageController(id);
        return {polls,clicks:localStorage.getItem('clicks'),state:h.readState().outcome,summary:h.readState().summary.message};
    }""")
    assert result == {
        "polls": 0,
        "clicks": None,
        "state": "AWAITING_MANUAL_CONFIRMATION",
        "summary": "Automatic operation paused; leader unavailable. Continue manually.",
    }


async def test_revoke_during_submission_journal_update_is_not_lost(chromium_page):
    first, second = await pages_for(chromium_page, BOOKING)
    first.on("dialog", lambda dialog: dialog.accept())
    await first.evaluate(f"""async () => {{
        await {HOOKS}.ensureSubmissionConsent({{unitId:'u',depId:'d',doctorId:'doc'}},{{memberId:'member'}},{{interactive:true}});
    }}""")
    # The short journal mutex serializes manual mutation with a transaction RMW.
    await first.evaluate(f"""() => {{
        navigator.locks.request({HOOKS}.JOURNAL_LOCK, async () => {{
            window.releaseJournal = null;
            await new Promise(resolve => {{window.releaseJournal = resolve;}});
        }});
    }}""")
    await first.wait_for_function("releaseJournal !== null")
    await second.evaluate(
        f"() => {{ window.revoked = false; {HOOKS}.revokeConsent().then(() => {{window.revoked = true;}}); }}"
    )
    assert await second.evaluate("revoked") is False
    await first.evaluate("releaseJournal()")
    await second.wait_for_function("revoked")
    assert await first.evaluate(f"{HOOKS}.readJournal().consents") == []


@pytest.mark.parametrize("loss", ["ttl", "hidden", "pagehide"])
async def test_pending_survives_owner_loss_and_only_readonly_takeover(
    chromium_page, loss
):
    first, second = await pages_for(chromium_page, BOOKING)
    if loss == "ttl":
        await first.clock.install()
    await first.evaluate("""() => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        h.withBrowserLeader(async () => {
            await h.beginAttempt(['synthetic-pending']);
            window.pendingWritten = true;
            await new Promise(() => {});
        });
    }""")
    await first.wait_for_function("window.pendingWritten === true")
    if loss == "ttl":
        await first.clock.fast_forward(31000)
    elif loss == "hidden":
        await first.evaluate(
            "Object.defineProperty(document,'visibilityState',{value:'hidden'}); document.dispatchEvent(new Event('visibilitychange'));"
        )
    else:
        await first.evaluate(
            "window.dispatchEvent(new PageTransitionEvent('pagehide',{persisted:true}));"
        )
    result = await second.evaluate("""async () => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        const readonly = await h.withBrowserLeader(() => ({owned:h.ownsBrowserLeader(),pending:h.submissionBlocked()}));
        h.startRun();
        const outcome = await h.submitTransaction(h.findSubmitControl(), {scheduleId:'other'}, {memberId:'member'}, {}, true);
        return {readonly,outcome,clicks:localStorage.getItem('clicks'),polls};
    }""")
    assert result == {
        "readonly": {"owned": True, "pending": True},
        "outcome": "OUTCOME_UNKNOWN",
        "clicks": None,
        "polls": 0,
    }


async def test_legacy_pending_cannot_be_erased_by_other_page_initialization(
    chromium_page,
):
    first, second = await pages_for(chromium_page, BOOKING)
    # Simulate a legacy page before the global journal exists. UI reads must not
    # create the salt by racing another renderer's initialization.
    await first.evaluate(f"""() => {{
        localStorage.removeItem({HOOKS}.JOURNAL_KEY);
        sessionStorage.setItem({HOOKS}.STATE_KEY,JSON.stringify({{submittingBooking:{{memberId:'synthetic-old'}}}}));
        {HOOKS}.readState();
    }}""")
    await second.evaluate(f"{HOOKS}.withBrowserLeader(() => true)")
    assert await first.evaluate(f"{HOOKS}.submissionBlocked()") is True
    await first.evaluate(f"{HOOKS}.withBrowserLeader(() => true)")
    assert await second.evaluate(f"{HOOKS}.submissionBlocked()") is True
    assert await second.evaluate(f"{HOOKS}.readJournal().attempts.length") == 1


async def test_lost_owner_does_not_fallback_to_a_new_transport(chromium_page):
    first, second = await pages_for(chromium_page)
    await first.evaluate("""() => {
        window.fetchCalls = 0;
        window.fetch = async () => {fetchCalls++; return {text:async () => '{}',status:200};};
        window.jQuery.ajax = options => { polls++; window.failPoll = options.error; };
    }""")
    await first.evaluate(START_DOCTOR)
    await first.wait_for_function("polls === 1")
    await first.evaluate(f"{HOOKS}.pauseForLeaderLoss()")
    await first.wait_for_function("finished")
    await second.evaluate(START_DOCTOR)
    await second.wait_for_function("polls === 1")
    await first.evaluate("failPoll({status:0}, 'timeout', 'synthetic')")
    assert await first.evaluate("fetchCalls") == 0
    await second.evaluate(f"{HOOKS}.stopRun()")


async def test_legacy_pending_blocks_other_normal_booking_controller(chromium_page):
    first, second = await pages_for(chromium_page, BOOKING)
    await first.evaluate(f"""() => {{
        localStorage.removeItem({HOOKS}.JOURNAL_KEY);
        sessionStorage.setItem({HOOKS}.STATE_KEY, JSON.stringify({{submittingBooking:{{memberId:'synthetic-old'}}}}));
        {HOOKS}.readState();
    }}""")
    second.on("dialog", lambda dialog: dialog.accept())
    result = await second.evaluate("""async () => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        const id = h.prepareManualControllerStart('booking');
        await h.runBookingPageController(id);
        return {clicks:localStorage.getItem('clicks'),pending:h.submissionBlocked(),journal:h.readJournal()};
    }""")
    assert result["clicks"] is None
    assert result["pending"] is True
    assert len(result["journal"]["attempts"]) == 1
    assert result["journal"]["attempts"][0]["failure_class"] == "interrupted"


async def test_old_delayed_navigation_cannot_borrow_new_owner(chromium_page):
    first, _ = await pages_for(chromium_page, BOOKING)
    await first.clock.install()
    await first.evaluate("""() => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        h.writeSettings({session:{recoveryCooldownMs:[3000,3000]},pacing:{rateLimitCooldownMs:[15000,15000]}});
    }""")
    await first.evaluate("""() => {
        document.body.append('访问次数过多');
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        const id = h.prepareManualControllerStart('booking');
        h.runBookingPageController(id).then(() => {finished = true;});
    }""")
    await first.wait_for_function(
        f"{HOOKS}.readState().summary?.message === 'Booking page hit rate limiting.'"
    )
    await first.evaluate(f"""() => {{
        {HOOKS}.pauseForLeaderLoss();
        history.replaceState(null,'','/doctors/index/unit_id-u/dep_id-d/docid-doc.html');
        window.marker = 'new-owner';
    }}""")
    await first.wait_for_function("finished")
    await first.evaluate(START_DOCTOR)
    await first.wait_for_function("polls === 1")
    await first.clock.fast_forward(15100)
    assert await first.evaluate("window.marker") == "new-owner"
    assert await first.evaluate(f"{HOOKS}.ownsBrowserLeader()") is True
    await first.evaluate(f"{HOOKS}.stopRun()")
