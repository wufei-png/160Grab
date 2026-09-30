import json

import pytest

from tests.userscripts.test_doctor_page_poller import _run_hook


@pytest.mark.parametrize(
    "effect", ["timeout", "navigation", "rate-limit", "lost-response", "weak-success"]
)
def test_single_click_and_all_runtime_actions_remain_blocked(effect):
    result = _run_hook(
        """(async () => {
      let clicks = 0;
      const outcome = await hooks.submitTransaction({ method: 'selector', element: { click() { clicks++; if (!['weak-success', 'rate-limit'].includes(effect)) throw new Error('synthetic'); } } }, { scheduleId:'slot', appointmentValue:'time' }, { memberId:'SYN_MEMBER' }, { unitId:'u', depId:'d', doctorId:'doc' }, true);
      hooks.stopRun(); hooks.resetRuntimeState(); hooks.startRun();
      const second = await hooks.submitTransaction({ method:'selector', element:{click(){ clicks++; }} }, {scheduleId:'other'}, {memberId:'other'}, {}, true);
      return { clicks, outcome, second, blocked:hooks.submissionBlocked(), journal:hooks.readJournal() };
    })()""",
        extra_js=f"""const effect = {json.dumps(effect)}; sandbox.console.log = sandbox.console.warn = () => undefined;
const body = {{innerHTML: '', querySelector:()=>null}};
sandbox.document.getElementById = () => ({{querySelector:()=>body}});""",
    )
    assert result["clicks"] == 1
    assert result["outcome"] == result["second"] == "OUTCOME_UNKNOWN"
    assert result["blocked"]
    assert "SYN_MEMBER" not in json.dumps(result)


@pytest.mark.parametrize("state", ["CONFIRMED_SUCCESS", "CONFIRMED_NO_EFFECT"])
@pytest.mark.parametrize("matched", [True, False])
def test_matching_business_evidence_only(state, matched):
    result = _run_hook(f"""(async () => {{
      const selection = ['u', 'd', 'doc', 'member', 'slot', 'time'];
      const outcome = await hooks.submitTransaction({{method:'selector', element:{{click(){{}}}}}}, {{scheduleId:'slot', appointmentValue:'time'}}, {{memberId:'member'}}, {{unitId:'u', depId:'d', doctorId:'doc'}}, true, async () => ({{state:{json.dumps(state)}, selection:{"selection" if matched else "['wrong']"}}}));
      return {{outcome, journal:hooks.readJournal()}};
    }})()""")
    assert result["outcome"] == (state if matched else "OUTCOME_UNKNOWN")


@pytest.mark.parametrize("fault", ["corrupt", "version", "write"])
def test_storage_fault_no_click(fault):
    setup = {
        "corrupt": "sandbox.localStorage.setItem(hooks.JOURNAL_KEY, '{');",
        "version": "sandbox.localStorage.setItem(hooks.JOURNAL_KEY, JSON.stringify({version:99}));",
        "write": "sandbox.localStorage.setItem = () => { throw new Error('quota'); };",
    }[fault]
    result = _run_hook(
        """(async () => {
      let clicks = 0;
      const outcome = await hooks.submitTransaction({method:'selector', element:{click(){clicks++;}}}, {scheduleId:'slot'}, {memberId:'member'}, {}, true);
      return {outcome, clicks};
    })()""",
        extra_js=setup,
    )
    assert result == {"outcome": "OUTCOME_UNKNOWN", "clicks": 0}


def test_unauthorized_never_clicks():
    assert _run_hook("""(async () => {
      let clicks = 0;
      const outcome = await hooks.submitTransaction({method:'selector', element:{click(){clicks++;}}}, {}, {}, {});
      return {outcome, clicks};
    })()""") == {"outcome": "AWAITING_MANUAL_CONFIRMATION", "clicks": 0}


def test_legacy_submitting_is_migrated_without_sensitive_fields():
    result = _run_hook(
        """(() => {
      sandbox.sessionStorage.setItem(hooks.STATE_KEY, JSON.stringify({submittingBooking:{memberId:'SYN_MEMBER', address:'SYN_ADDRESS'}}));
      hooks.readState(); hooks.stopRun(); hooks.resetRuntimeState();
      return {blocked:hooks.submissionBlocked(), journal:hooks.readJournal()};
    })()""",
        extra_js="""sandbox.console.log = sandbox.console.warn = () => undefined;
const body = {innerHTML: '', querySelector:()=>null};
sandbox.document.getElementById = () => ({querySelector:()=>body});""",
    )
    assert result["blocked"]
    assert "SYN_" not in json.dumps(result)
