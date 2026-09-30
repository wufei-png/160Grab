from tests.userscripts.test_doctor_page_poller import _run_hook


def test_ajax_failure_has_no_fetch_fallback_and_retry_after_is_kept():
    result = _run_hook(
        "hooks.fetchJsonInsidePage('https://gate.91160.com/guahao/v1/pc/sch/doctor', {}).catch(e => ({state:e.sessionState, hint:e.retryAfterMs, calls:sandbox.calls}))",
        """
    sandbox.calls = 0;
    sandbox.fetch = () => { sandbox.calls++; throw new Error('second transport forbidden'); };
    sandbox.jQuery = {ajax: options => {sandbox.calls++;options.error({status:429,getResponseHeader:()=> '60'});}};
    """,
    )
    assert result == {"state": "RATE_LIMITED", "hint": 60000, "calls": 1}


def test_http_status_redirect_and_bad_json_are_classified_without_server_text():
    result = _run_hook("""(() => {
        const cases=[[503,null],[429,null],[200,null],[200,{},null,'https://user.91160.com/login.html?secret=SYN_KEY']];
        return cases.map(args => {try {hooks.readResponse(...args);} catch(e){return {state:e.sessionState,message:e.message};}});
    })()""")
    assert [r["state"] for r in result] == [
        "TRANSIENT_FAILURE",
        "RATE_LIMITED",
        "UNKNOWN",
        "EXPIRED",
    ]
    assert all("SYN_KEY" not in r["message"] for r in result)


def test_retry_after_accepts_delta_seconds_and_http_date():
    result = _run_hook(
        "[hooks.retryAfterMs('45'),hooks.retryAfterMs('Thu, 01 Oct 2026 00:01:00 GMT',Date.parse('2026-10-01T00:00:00Z')),hooks.retryAfterMs('bad'),hooks.retryAfterMs('Infinity')]"
    )
    assert result == [45000, 60000, 0, 0]
