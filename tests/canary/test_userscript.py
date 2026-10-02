import pytest

from tests.contracts.booking.scenarios import USERSCRIPT


async def load(page, body, url):
    await page.context.route(
        "**/*", lambda route: route.fulfill(body=body, content_type="text/html")
    )
    await page.add_init_script(
        "window.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__=true;"
    )
    await page.goto(url)
    await page.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
    await page.evaluate("""() => {
        window.clicks=0; window.changes=0;
        document.addEventListener('click',()=>clicks++);
        document.addEventListener('change',()=>changes++);
        __GRAB160_DOCTOR_POLLER_TEST_HOOKS__.writeSettings({booking:{submitMode:'auto'}, filters:{hours:['9-10']}, pacing:{pageActionMs:[0,0]}});
    }""")


async def test_userscript_prepare_never_submits_even_valid_consent(chromium_page):
    page = chromium_page
    page.on("dialog", lambda dialog: dialog.accept())
    await load(
        page,
        '<input name="schedule_id" value="slot"><input type="hidden" name="mid" value="member"><button id="submitbtn">预约</button>',
        "https://synthetic.invalid/guahao/ystep1/uid-u/depid-d/schid-slot.html",
    )
    # Hour-specific preparation needs an actual time option.
    await page.evaluate("""() => {
        __GRAB160_DOCTOR_POLLER_TEST_HOOKS__.writeSettings({filters:{hours:[]}});
    }""")
    result = await page.evaluate("""async () => {
        const h=__GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        h.writeSettings({filters:{hours:['9-10']}});
        document.body.insertAdjacentHTML('beforeend', '<ul id="delts"><li val="time" class="selected">09:00-09:30</li></ul>');
        const target={unitId:'u',depId:'d',doctorId:'doc'};
        await h.withBrowserLeader(()=>h.ensureSubmissionConsent(target,{memberId:'member'},{interactive:true}));
        return await h.runCanary('prepare',{date:'2026-09-30',target,memberId:'member',scheduleId:'slot',LIVE_E2E:1,LIVE_BOOKING:1,ready:async()=>true});
    }""")
    assert result["status"] == "prepared" and result["submitCalls"] == 0
    assert await page.evaluate("clicks") == 0
    assert (
        await page.evaluate("""async () => {
        const h=__GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        return await h.submitTransaction(h.findSubmitControl(),{scheduleId:'slot'},{memberId:'member'},{unitId:'u',depId:'d',doctorId:'doc'},true);
    }""")
        == "AWAITING_MANUAL_CONFIRMATION"
    )
    await page.reload()
    await page.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
    await page.evaluate("""async () => {
        const h=__GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        const id=h.prepareManualControllerStart('booking'); await h.runBookingPageController(id);
    }""")
    assert await page.evaluate('document.querySelector("#submitbtn") !== null')
    assert not await page.evaluate(
        "__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.submissionBlocked()"
    )


@pytest.mark.parametrize("e2e,booking", [(0, 0), (1, 0), (0, 1)])
async def test_userscript_submit_requires_both_flags(chromium_page, e2e, booking):
    page = chromium_page
    await load(
        page,
        '<button id="submitbtn">预约</button>',
        "https://synthetic.invalid/booking",
    )
    result = await page.evaluate(
        """async ([e,b]) => __GRAB160_CANARY__.run('submit',{LIVE_E2E:e,LIVE_BOOKING:b,ready:async()=>true})""",
        [e2e, booking],
    )
    assert result == {
        "status": "submit_gate_blocked",
        "level": "submit",
        "polls": 0,
        "submitCalls": 0,
        "state": None,
    }
    assert await page.evaluate("clicks") == 0


async def test_userscript_readonly_resolves_docid_and_never_changes_form(chromium_page):
    page = chromium_page
    await load(
        page,
        '<a id="addMark" doctor_id="doc" unit_id="u" dep_id="d"></a><input type="radio" name="mid" value="member"><input id="hismemid">',
        "https://synthetic.invalid/doctors/index/docid-doc.html",
    )
    # The site-origin case is still entirely routed to synthetic content.
    await page.goto("https://www.91160.com/doctors/index/docid-doc.html")
    await page.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
    await page.evaluate("""() => {
        window.clicks=0; window.changes=0; window.requests=0; window._user_key='SYN_KEY';
        document.addEventListener('click',()=>clicks++);document.addEventListener('change',()=>changes++);
        window.fetch=async()=>{requests++;return {status:200,text:async()=>JSON.stringify({result_code:1,data:{schedules:[{schedule_id:'slot',doctor_id:'doc',unit_id:'u',dep_id:'d',date:'2026-09-30',status:'available',weekday:3,day_period:'am'}]}})}};
    }""")
    result = await page.evaluate(
        """async () => __GRAB160_CANARY__.run('readonly',{target:{unitId:'u',depId:'d',doctorId:'doc'},date:'2026-09-30',ready:async()=>true})"""
    )
    assert result["status"] == "observed" and result["polls"] == 1
    assert await page.evaluate(
        '[clicks,changes,requests,document.querySelector("input[type=radio]").checked,document.querySelector("#hismemid").value]'
    ) == [0, 0, 1, False, ""]


async def test_userscript_ready_timeout_fences_late_continuation(chromium_page):
    page = chromium_page
    await load(
        page,
        '<button id="submitbtn">预约</button>',
        "https://synthetic.invalid/booking",
    )
    result = await page.evaluate("""async () => {
        let release; window.readyPromise = new Promise(r=>release=r); window.releaseReady=release;
        return await __GRAB160_CANARY__.run('prepare',{target:{unitId:'u',depId:'d',doctorId:'doc'},date:'2026-09-30',memberId:'member',scheduleId:'slot',timeoutMs:1000,ready:()=>readyPromise});
    }""")
    assert result["status"] == "inconclusive_timeout"
    await page.evaluate("releaseReady(true)")
    assert await page.evaluate("clicks") == 0


async def test_userscript_empty_slots_stop_at_count_budget(chromium_page):
    page = chromium_page
    await load(
        page,
        '<a id="addMark" doctor_id="doc" unit_id="u" dep_id="d"></a>',
        "https://synthetic.invalid/doctors/index/docid-doc.html",
    )
    await page.evaluate("""() => { window._user_key='SYN_KEY'; window.requests=0;
        window.fetch=async()=>{requests++;return {status:200,text:async()=>'{"result_code":1,"data":{"schedules":[]}}'}}; }""")
    result = await page.evaluate(
        """async () => __GRAB160_CANARY__.run('readonly',{target:{unitId:'u',depId:'d',doctorId:'doc'},date:'2026-09-30',maxPolls:1,ready:async()=>true})"""
    )
    assert result["status"] == "inconclusive_no_slots"
    assert result["polls"] == 1 and await page.evaluate("requests") == 1


@pytest.mark.parametrize("blocker", ["unavailable", "hidden", "contended", "journal"])
async def test_userscript_canary_owner_blockers_report_status_and_counts(
    chromium_page, blocker
):
    page = chromium_page
    await load(
        page,
        '<a id="addMark" doctor_id="doc" unit_id="u" dep_id="d"></a><button id="submitbtn">预约</button>',
        "https://synthetic.invalid/doctors/index/docid-doc.html",
    )
    await page.evaluate("""() => {
        window.requests=0; window._user_key='SYN_KEY';
        window.fetch=async()=>{requests++;return {status:200,text:async()=>'{"result_code":1,"data":{"schedules":[]}}'}};
    }""")
    if blocker == "unavailable":
        await page.evaluate("Object.defineProperty(navigator,'locks',{value:undefined})")
    elif blocker == "hidden":
        await page.evaluate(
            "Object.defineProperty(document,'visibilityState',{value:'hidden'})"
        )
    elif blocker == "contended":
        await page.evaluate("""() => {
            navigator.locks.request(__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.LEADER_LOCK, async () => {
                await new Promise(resolve => {window.releaseOtherOwner=resolve;});
            });
        }""")
        await page.wait_for_function("typeof releaseOtherOwner === 'function'")
    else:
        await page.evaluate(
            "localStorage.setItem(__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.JOURNAL_KEY,'invalid-json')"
        )
    result = await page.evaluate(
        """async () => __GRAB160_CANARY__.run('readonly',{target:{unitId:'u',depId:'d',doctorId:'doc'},date:'2026-09-30',ready:async()=>true})"""
    )
    assert result == {
        "status": "stopped" if blocker == "journal" else "leader_blocked",
        "level": "readonly",
        "polls": 0,
        "submitCalls": 0,
        "state": "OUTCOME_UNKNOWN"
        if blocker == "journal"
        else "AWAITING_MANUAL_CONFIRMATION",
    }
    assert await page.evaluate("[clicks,changes,requests]") == [0, 0, 0]
    if blocker == "contended":
        await page.evaluate("releaseOtherOwner()")
    if blocker == "journal":
        assert await page.evaluate(
            "localStorage.getItem(__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.JOURNAL_KEY)"
        ) == "invalid-json"


async def test_userscript_canary_owner_loss_reports_inflight_read_and_fences_callback(
    chromium_page,
):
    page = chromium_page
    await load(
        page,
        '<a id="addMark" doctor_id="doc" unit_id="u" dep_id="d"></a><button id="submitbtn">预约</button>',
        "https://synthetic.invalid/doctors/index/docid-doc.html",
    )
    await page.evaluate("""() => {
        window.requests=0; window._user_key='SYN_KEY'; window.canaryResult=null;
        window.jQuery={ajax(options){requests++;window.respond=options.success;return {abort(){}};}};
        __GRAB160_CANARY__.run('readonly',{target:{unitId:'u',depId:'d',doctorId:'doc'},date:'2026-09-30',ready:async()=>true}).then(result=>{window.canaryResult=result;});
    }""")
    await page.wait_for_function("requests === 1")
    await page.evaluate("""() => {
        Object.defineProperty(document,'visibilityState',{value:'hidden'});
        document.dispatchEvent(new Event('visibilitychange'));
    }""")
    await page.wait_for_function("canaryResult !== null")
    assert await page.evaluate("canaryResult") == {
        "status": "leader_blocked",
        "level": "readonly",
        "polls": 1,
        "submitCalls": 0,
        "state": "AWAITING_MANUAL_CONFIRMATION",
    }
    await page.evaluate(
        "respond({result_code:1,data:{schedules:[{schedule_id:'slot',doctor_id:'doc',unit_id:'u',dep_id:'d',date:'2026-09-30',status:'available',weekday:3,day_period:'am'}]}})"
    )
    assert await page.evaluate("[clicks,changes,requests]") == [0, 0, 1]
    assert await page.evaluate("location.pathname") == "/doctors/index/docid-doc.html"
    assert await page.evaluate(
        "sessionStorage.getItem(__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.CANARY_KEY)"
    ) == "locked"


async def test_userscript_submit_needs_specific_approval_and_never_claims_success(
    chromium_page,
):
    page = chromium_page
    await load(
        page,
        '<input name="schedule_id" value="slot"><input type="hidden" name="mid" value="member"><ul id="delts"><li val="time" class="selected">09:00-09:30</li></ul><button id="submitbtn">预约</button>',
        "https://synthetic.invalid/guahao/ystep1/uid-u/depid-d/schid-slot.html",
    )
    page.on("dialog", lambda dialog: dialog.accept())
    result = await page.evaluate("""async () => {
        const options={target:{unitId:'u',depId:'d',doctorId:'doc'},date:'2026-09-30',memberId:'member',scheduleId:'slot',LIVE_E2E:1,LIVE_BOOKING:1};
        const rejected=await __GRAB160_CANARY__.run('submit',{...options,ready:async phase=>phase!=='submit'});
        const accepted=await __GRAB160_CANARY__.run('submit',{...options,ready:async()=>true});
        const blocked=await __GRAB160_CANARY__.run('submit',{...options,ready:async()=>true});
        return {rejected,accepted,blocked};
    }""")
    assert result["rejected"]["status"] == "submit_not_approved"
    assert result["accepted"]["state"] == "OUTCOME_UNKNOWN"
    assert result["blocked"]["status"] == "pending_blocked"
    assert await page.evaluate("clicks") == 1


async def test_userscript_panel_can_disable_legacy_auto_start(chromium_page):
    page = chromium_page
    await page.context.route(
        "**/*",
        lambda route: route.fulfill(
            body='<a id="addMark" doctor_id="doc" unit_id="u" dep_id="d"></a>',
            content_type="text/html",
        ),
    )
    await page.goto("https://synthetic.invalid/doctors/index/docid-doc.html")
    await page.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
    await page.evaluate("""() => {
        __GRAB160_DOCTOR_POLLER_TEST_HOOKS__.writeSettings({runtime:{autoStart:true}});
    }""")
    await page.locator('[data-action="settings"]').click()
    toggle = page.locator('[data-setting="runtime.autoStart"]')
    assert await toggle.is_checked()
    await toggle.uncheck()
    await page.locator('[data-save-settings]').click()
    assert not await page.evaluate(
        "__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.readSettings().runtime.autoStart"
    )
    await page.reload()
    await page.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
    assert not await page.evaluate(
        "__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.readState().running"
    )


async def test_userscript_panel_canary_works_without_page_global_bridge(chromium_page):
    page = chromium_page
    await page.context.route(
        "**/*",
        lambda route: route.fulfill(
            body='<a id="addMark" doctor_id="doc" unit_id="u" dep_id="d"></a>',
            content_type="text/html",
        ),
    )
    await page.goto("https://synthetic.invalid/doctors/index/docid-doc.html")
    await page.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
    await page.evaluate("""() => {
        const h=__GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        h.writeSettings({target:{unitId:'u',depId:'d',doctorId:'doc'},filters:{startDate:'2026-09-30'}});
        window._user_key='SYN_KEY';window.fetch=async()=>({status:200,text:async()=>'{"result_code":1,"data":{"schedules":[]}}'});
    }""")
    assert await page.locator("[data-canary-level]").input_value() == "readonly"
    assert not await page.locator("[data-canary-e2e]").is_checked()
    await page.get_by_text("本机人工 canary", exact=True).click()
    await page.locator("[data-canary-arm]").click()
    assert (
        await page.evaluate("__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.readState().running")
        is False
    )
    # Exercise the actual panel event with the quick submit gate; no site click.
    await page.get_by_text("本机人工 canary", exact=True).click()
    await page.locator("[data-canary-level]").select_option("submit")
    messages = []

    async def dialog_handler(dialog):
        messages.append(dialog.message)
        await dialog.accept()

    page.on("dialog", dialog_handler)
    await page.locator("[data-canary-run]").click()
    await page.wait_for_function(
        "__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.readState().running === false"
    )
    assert messages and "submit_gate_blocked" in messages[-1]
