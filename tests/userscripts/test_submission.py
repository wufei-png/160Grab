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


@pytest.mark.parametrize("legacy,mode", [(True, "auto"), (False, "manual_confirm")])
def test_legacy_boolean_migrates_without_authorization(legacy, mode):
    result = _run_hook(f"""(async () => {{
      const settings = hooks.normalizeSettings({{booking:{{autoSubmit:{json.dumps(legacy)}, consent:true, maxSubmitAttemptsPerAppointment:20}}}});
      const allowed = await hooks.ensureSubmissionConsent({{unitId:'u',depId:'d',doctorId:'doc'}}, {{memberId:'member'}});
      return {{settings, allowed}};
    }})()""")
    assert result["settings"]["booking"]["submitMode"] == mode
    assert result["settings"]["booking"]["maxPreSubmitAttempts"] == 3
    assert result["allowed"] is False


def test_consent_bound_to_member_target_and_run():
    result = _run_hook("""(async () => {
      let prompts = [];
      sandbox.confirm = (text) => { prompts.push(text); return true; };
      const target = {unitId:'u',depId:'d',doctorId:'SYN_DOCTOR'};
      const member = {memberId:'SYN_MEMBER'};
      await hooks.ensureSubmissionConsent(target, member, {interactive:true});
      await hooks.ensureSubmissionConsent(target, member, {interactive:true});
      await hooks.ensureSubmissionConsent({...target,doctorId:'other'}, member, {interactive:true});
      await hooks.ensureSubmissionConsent(target, {memberId:'other'}, {interactive:true});
      return {prompts, journal:hooks.readJournal()};
    })()""")
    assert len(result["prompts"]) == 3
    assert "未知" in result["prompts"][0] and "禁止" in result["prompts"][0]
    assert "SYN_" not in json.dumps(result["journal"])


def test_rejected_consent_keeps_manual_mode():
    result = _run_hook("""(async () => {
      sandbox.confirm = () => false;
      let stored = null;
      sandbox.GM_setValue = (_key, value) => { stored = value; };
      const allowed = await hooks.ensureSubmissionConsent({unitId:'u',depId:'d',doctorId:'doc'}, {memberId:'member'}, {interactive:true});
      return {allowed, stored};
    })()""")
    assert result["allowed"] is False
    assert result["stored"]["booking"]["submitMode"] == "manual_confirm"


def test_human_resolution_leaves_audit_and_same_booking_cannot_repeat():
    result = _run_hook("""(async () => {
      const record = await hooks.beginAttempt(['SYN_BOOKING']);
      hooks.finishAttempt(record.attempt_id, 'CONFIRMED_NO_EFFECT', true);
      let blocked = false;
      try { await hooks.beginAttempt(['SYN_BOOKING']); } catch (_) { blocked = true; }
      return {blocked, journal:hooks.readJournal()};
    })()""")
    assert result["blocked"]
    assert result["journal"]["audit"][0]["state"] == "CONFIRMED_NO_EFFECT"
    assert "SYN_" not in json.dumps(result["journal"])


def test_revoke_preserves_pending_and_resolution_updates_panel_outcome():
    result = _run_hook(
        """(async () => {
      await hooks.ensureSubmissionConsent({unitId:'u',depId:'d',doctorId:'doc'}, {memberId:'member'}, {interactive:true});
      const record = await hooks.beginAttempt(['slot']);
      hooks.revokeConsent();
      const pending = hooks.submissionBlocked();
      hooks.resolvePending(false);
      return {pending, blocked:hooks.submissionBlocked(), state:hooks.readState().outcome, journal:hooks.readJournal()};
    })()""",
        extra_js="""sandbox.console.log = sandbox.console.warn = () => undefined;
const body = {innerHTML: '', querySelector:()=>null};
sandbox.document.getElementById = () => ({querySelector:()=>body});""",
    )
    assert result["pending"]
    assert not result["blocked"]
    assert result["state"] == "CONFIRMED_NO_EFFECT"
    assert result["journal"]["consents"] == []
    assert len(result["journal"]["audit"]) == 1


def test_prior_policy_is_not_current_consent():
    result = _run_hook("""(async () => {
      const target = {unitId:'u',depId:'d',doctorId:'doc'};
      const member = {memberId:'member'};
      await hooks.ensureSubmissionConsent(target, member, {interactive:true, accountRef:'account'});
      const journal = hooks.readJournal();
      journal.consents[0].policy_version = 'submit-v0';
      hooks.writeJournal(journal);
      const allowed = await hooks.ensureSubmissionConsent(target, member, {accountRef:'account'});
      return allowed;
    })()""")
    assert result is False


def test_stop_during_hashing_never_enters_click_boundary():
    result = _run_hook('''(async () => {
      let clicks = 0;
      const outcome = await hooks.submitTransaction({method:'selector',element:{click(){clicks++;}}}, {scheduleId:'slot'}, {memberId:'member'}, {}, true, null, () => false);
      return {clicks, pending:hooks.submissionBlocked(), outcome};
    })()''')
    assert result['clicks'] == 0
    assert not result['pending']
