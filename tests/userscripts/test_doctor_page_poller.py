import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "userscripts" / "91160-doctor-page-poller.user.js"


def _run_node_script(js_code: str) -> str:
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.fail("node is required to validate the userscript helpers")
    with tempfile.TemporaryDirectory() as tmp_dir:
        script_path = Path(tmp_dir) / "userscript-hook-test.js"
        script_path.write_text(js_code, encoding="utf-8")
        completed = subprocess.run(
            [node_bin, str(script_path)],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    return completed.stdout.strip()


def _run_hook(expression: str, extra_js: str = ""):
    script_source = json.dumps(SCRIPT_PATH.read_text(encoding="utf-8"))
    code = f"""
const vm = require("node:vm");
const source = {script_source};
const sandbox = {{
  console,
  crypto: require("node:crypto").webcrypto,
  Uint8Array,
  TextEncoder,
  TextDecoder,
  URL,
  Math,
  JSON,
  Date,
  Number,
  String,
  setTimeout,
  clearTimeout,
  performance: require("node:perf_hooks").performance,
  navigator: {{ locks: {{
    tails: new Map(),
    async request(name, options, callback) {{
      if (typeof options === 'function') {{ callback = options; options = {{}}; }}
      const previous = this.tails.get(name);
      if (previous && options.ifAvailable) return callback(null);
      let release;
      const gate = new Promise(resolve => {{ release = resolve; }});
      this.tails.set(name, gate);
      if (previous) await previous;
      try {{ return await callback({{name}}); }}
      finally {{ if (this.tails.get(name) === gate) this.tails.delete(name); release(); }}
    }},
  }} }},
  location: new URL("https://www.91160.com/doctors/index/unit_id-21/dep_id-0/docid-200002522.html"),
  document: {{
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: () => null,
    createElement: () => ({{ style: {{}}, dataset: {{}}, addEventListener() {{}}, appendChild() {{}}, querySelector: () => null, querySelectorAll: () => [] }}),
    documentElement: {{ appendChild() {{}}, outerHTML: "", innerText: "", textContent: "" }},
    body: {{ innerText: "", textContent: "" }},
  }},
  sessionStorage: {{
    store: {{}},
    getItem(k) {{ return this.store[k] ?? null; }},
    setItem(k, v) {{ this.store[k] = String(v); }},
    removeItem(k) {{ delete this.store[k]; }},
  }},
  localStorage: {{
    store: {{}},
    getItem(k) {{ return this.store[k] ?? null; }},
    setItem(k, v) {{ this.store[k] = String(v); }},
    removeItem(k) {{ delete this.store[k]; }},
  }},
  fetch: async () => ({{ text: async () => "{{}}", status: 200 }}),
  getComputedStyle: () => ({{ display: "block", visibility: "visible" }}),
  MouseEvent: class MouseEvent {{
    constructor(type) {{ this.type = type; }}
  }},
  Event: class Event {{
    constructor(type) {{ this.type = type; }}
  }},
  GM_getValue: (_key, fallback) => fallback,
  GM_setValue: () => undefined,
  GM_deleteValue: () => undefined,
  confirm: () => true,
  __GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__: true,
}};
sandbox.globalThis = sandbox;
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.unsafeWindow = sandbox;
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const hooks = sandbox.__GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
{extra_js}
(async () => {{
  const result = await ({expression});
  process.stdout.write(JSON.stringify(result));
}})().catch((error) => {{
  console.error(error && error.stack ? error.stack : error);
  process.exit(1);
}});
"""
    output = _run_node_script(code)
    return json.loads(output)


def test_userscript_metadata_matches_tampermonkey_storage_design():
    content = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "@match        https://www.91160.com/doctors/index/*" in content
    assert "@match        https://www.91160.com/guahao/ystep1/*" in content
    assert "@grant        GM_getValue" in content
    assert "@grant        GM_setValue" in content
    assert "@grant        GM_deleteValue" in content
    assert "@grant        unsafeWindow" in content
    assert "GM_xmlhttpRequest" not in content
    assert 'credentials: "omit"' in content
    assert "// @version      0.3.0" in content
    assert 'const SCRIPT_VERSION = "0.3.0";' in content
    assert "160Grab v${SCRIPT_VERSION}" in content


def test_panel_markup_uses_prefixed_classes_and_non_submit_buttons():
    content = SCRIPT_PATH.read_text(encoding="utf-8")

    for class_name in [
        '"buttons"',
        '"row"',
        '"muted"',
        '"warn"',
        '"error"',
        '"success"',
        '"log"',
    ]:
        assert f"class={class_name}" not in content
        assert f"className = {class_name}" not in content

    assert "<button data-" not in content
    assert "button:disabled" not in content
    assert '${state.running ? "Restart" : "Start"}' in content
    assert '<button type="button"' in content
    assert "grab160-status-success" in content
    assert "grab160-buttons" in content
    assert "Unit ID" not in content
    assert "Dep ID" not in content
    assert "Doctor ID" not in content
    assert "target=" not in content
    assert "data-panel-target>${htmlEscape(panelDoctorText(state))}" in content
    assert "Start Date" not in content
    assert "Appointment From" in content
    assert "PANEL_TOOLTIP_ID" in content
    assert 'data-help="${htmlEscape(text)}"' in content


def test_userscript_passes_node_syntax_check():
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("node is required to syntax-check the userscript")
    subprocess.run([node_bin, "--check", str(SCRIPT_PATH)], cwd=REPO_ROOT, check=True)


def test_normalize_settings_keeps_python_config_semantics_for_business_fields():
    result = _run_hook(
        """hooks.normalizeSettings({
  settingsVersion: 4,
  runtime: { autoStart: true, startAt: "2026-06-28T08:00" },
  target: { unitId: "21", depId: "4385", doctorId: "200002522" },
  member: { memberId: "147750901" },
  address: { province: "广东", city: "深圳", area: "南山", detail: "深南花园" },
  filters: {
    startDate: "2026-06-29",
    weeks: [1, "3", 9],
    days: ["am", "pm", "bad"],
    hours: ["8-9", "9.5-10", "14:00-14:30"]
  },
  pacing: { pollMs: [5000, 3000] },
  booking: {
    autoSubmit: true,
    maxSubmitAttemptsPerAppointment: "4",
    diseaseDescription: "门诊就诊，具体病情现场面诊沟通"
  },
  session: { keepAliveIntervalSeconds: "180", recoveryMaxAttempts: "2" },
  logging: { level: "debug", maxEntries: "25" },
})""",
    )

    assert result["runtime"]["autoStart"] is True
    assert result["filters"]["hours"] == [
        "08:00-09:00",
        "09:30-10:00",
        "14:00-14:30",
    ]
    assert result["filters"]["weeks"] == [1, 3]
    assert result["filters"]["days"] == ["am", "pm"]
    assert result["pacing"]["pollMs"] == [3000, 5000]
    assert result["pacing"]["bookingSubmitSettleMs"] == [3500, 4500]
    assert result["address"] == {
        "province": "广东",
        "city": "深圳",
        "area": "南山",
        "detail": "深南花园",
    }
    assert result["booking"]["autoSubmit"] is True
    assert result["booking"]["maxPreSubmitAttempts"] == 3
    assert result["booking"]["diseaseDescription"] == "门诊就诊，具体病情现场面诊沟通"
    assert result["session"]["keepAliveIntervalSeconds"] == 180
    assert result["logging"]["level"] == "debug"


def test_normalize_settings_rejects_non_half_hour_precision():
    result = _run_hook(
        """(() => {
  try {
    hooks.normalizeSettings({ filters: { hours: ["9:15-10:00"] } });
    return "accepted";
  } catch (error) {
    return error.message;
  }
})()""",
    )

    assert "00 or 30 minute" in result


def test_normalize_settings_clamps_polling_and_cooldown_minimums():
    result = _run_hook(
        """hooks.normalizeSettings({
  pacing: {
    pollMs: [500, 1000],
    bookingRetryMs: [10, 20],
    rateLimitCooldownMs: [1000, 2000]
  }
})""",
    )

    assert result["pacing"]["pollMs"] == [3000, 3000]
    assert result["pacing"]["bookingRetryMs"] == [1000, 1000]
    assert result["pacing"]["rateLimitCooldownMs"] == [15000, 15000]


def test_booking_submit_settle_only_applies_after_doctor_page_auto_open():
    result = _run_hook(
        """[
  hooks.resolveBookingSubmitSettleMs({ pacing: { bookingSubmitSettleMs: [12, 12] } }, true),
  hooks.resolveBookingSubmitSettleMs({ pacing: { bookingSubmitSettleMs: [12, 12] } }, false)
]"""
    )

    assert result == [12, 0]


def test_controller_claim_is_singleton_per_page_instance():
    result = _run_hook(
        """hooks.withBrowserLeader(async () => {
  sandbox.sessionStorage.setItem(hooks.STATE_KEY, JSON.stringify({ running: true }));
  const first = hooks.claimPageController("doctor");
  const second = hooks.claimPageController("doctor");
  const stateAfterSecond = JSON.parse(sandbox.sessionStorage.getItem(hooks.STATE_KEY));
  return {
    firstIsActive: hooks.isControllerActive(first),
    second,
    stateControllerId: stateAfterSecond.controllerId,
    sameController: stateAfterSecond.controllerId === first,
  };
})""",
    )

    assert result["firstIsActive"] is True
    assert result["second"] is None
    assert result["sameController"] is True
    assert result["stateControllerId"].startswith("doctor:")


def test_manual_start_restarts_active_controller():
    result = _run_hook(
        """hooks.withBrowserLeader(async () => {
  sandbox.sessionStorage.setItem(hooks.STATE_KEY, JSON.stringify({ running: true }));
  const first = hooks.prepareManualControllerStart("doctor");
  const second = hooks.prepareManualControllerStart("doctor");
  const stateAfterSecond = JSON.parse(sandbox.sessionStorage.getItem(hooks.STATE_KEY));
  return {
    first,
    second,
    firstActive: hooks.isControllerActive(first),
    secondActive: hooks.isControllerActive(second),
    stateControllerId: stateAfterSecond.controllerId,
    pollAttempt: stateAfterSecond.pollAttempt,
  };
})""",
    )

    assert result["first"].startswith("doctor:")
    assert result["second"].startswith("doctor:")
    assert result["first"] != result["second"]
    assert result["firstActive"] is False
    assert result["secondActive"] is True
    assert result["stateControllerId"] == result["second"]
    assert result["pollAttempt"] == 0


def test_find_current_user_key_falls_back_to_access_hash_cookie():
    result = _run_hook(
        """[
  hooks.readCookieValue("access_hash", "foo=1; access_hash=cookie-user-key; bar=2"),
  hooks.resolveCurrentUserKey().source,
  hooks.findCurrentUserKey()
]""",
        extra_js='sandbox.document.cookie = "foo=1; access_hash=cookie-user-key; bar=2";',
    )

    assert result == [
        "cookie-user-key",
        "access_hash-cookie",
        "cookie-user-key",
    ]


def test_find_current_user_key_reads_page_global_from_unsafe_window():
    result = _run_hook(
        "hooks.resolveCurrentUserKey()",
        extra_js="""
sandbox._user_key = "";
sandbox.unsafeWindow = { _user_key: "unsafe-window-user-key" };
""",
    )

    assert result == {
        "userKey": "unsafe-window-user-key",
        "source": "unsafe-window",
    }


def test_find_current_user_key_uses_cached_value_when_live_sources_disappear():
    result = _run_hook(
        """(() => {
  sandbox.document.cookie = "access_hash=cached-user-key";
  hooks.findCurrentUserKey();
  sandbox.document.cookie = "";
  return hooks.resolveCurrentUserKey();
})()""",
    )

    assert result == {
        "userKey": "cached-user-key",
        "source": "access_hash-cookie",
    }


def test_fetch_json_inside_page_prefers_page_jquery():
    result = _run_hook(
        """hooks.fetchJsonInsidePage("https://gate.91160.com/guahao/v1/pc/sch/doctor", {
  user_key: "cookie-user-key",
  unit_id: "21",
  dep_id: "4380",
  docid: "9154",
  doc_id: "9154",
  date: "2026-06-28",
  days: "6",
}).then((payload) => ({ payload, ajaxCalls: sandbox.ajaxCalls }))""",
        extra_js="""
sandbox.ajaxCalls = [];
sandbox.jQuery = {
  ajax: (options) => {
    sandbox.ajaxCalls.push({ url: options.url, data: options.data });
    options.success({ code: 1, sch: { from: "jquery" } });
  },
};
sandbox.fetch = () => Promise.reject(new Error("plain fetch should not be used"));
""",
    )

    assert result["payload"] == {"code": 1, "sch": {"from": "jquery"}}
    assert result["ajaxCalls"] == [
        {
            "url": "https://gate.91160.com/guahao/v1/pc/sch/doctor",
            "data": {
                "user_key": "cookie-user-key",
                "unit_id": "21",
                "dep_id": "4380",
                "docid": "9154",
                "doc_id": "9154",
                "date": "2026-06-28",
                "days": "6",
            },
        }
    ]


def test_fetch_json_inside_page_falls_back_to_page_fetch():
    result = _run_hook(
        """hooks.fetchJsonInsidePage("https://gate.91160.com/guahao/v1/pc/sch/doctor", {
  user_key: "cookie-user-key",
  unit_id: "21",
  dep_id: "4380",
  docid: "9154",
  doc_id: "9154",
  date: "2026-06-28",
  days: "6",
}).then((payload) => ({ payload, fetchUrls: sandbox.fetchUrls }))""",
        extra_js="""
sandbox.fetchUrls = [];
sandbox.jQuery = {
  ajax: (options) => options.error({ status: 0, responseText: "" }, "error", ""),
};
sandbox.fetch = async (url) => {
  sandbox.fetchUrls.push(url);
  return {
    status: 200,
    text: async () => JSON.stringify({ code: 1, sch: { from: "fetch" } }),
  };
};
""",
    )

    assert result["payload"] == {"code": 1, "sch": {"from": "fetch"}}
    request_url = result["fetchUrls"][0]
    assert request_url.startswith("https://gate.91160.com/guahao/v1/pc/sch/doctor?")
    assert "user_key=cookie-user-key" in request_url
    assert "unit_id=21" in request_url
    assert "dep_id=4380" in request_url


def test_fetch_json_inside_page_reports_page_transport_failure():
    result = _run_hook(
        """hooks.fetchJsonInsidePage("https://gate.91160.com/guahao/v1/pc/sch/doctor", {})
  .then(() => "accepted")
  .catch((error) => error.message)""",
        extra_js="""
sandbox.jQuery = {
  ajax: (options) => options.error({ status: 0, responseText: "" }, "error", ""),
};
sandbox.fetch = () => Promise.reject(new Error("plain fetch failed"));
""",
    )

    assert result == "Page schedule request failed: plain fetch failed"


def test_resolve_target_from_dep_zero_snapshot_prefers_real_dep_from_dom():
    result = _run_hook(
        "hooks.resolveTargetFromSnapshot(snapshot, null)",
        extra_js="""
const snapshot = {
  href: "https://www.91160.com/doctors/index/unit_id-21/dep_id-0/docid-200002522.html",
  addMarkAttrs: {
    unit_id: "21",
    dep_id: "4385",
    doctor_id: "200002522",
  },
  doctorLinks: [
    "https://www.91160.com/doctors/index/unit_id-21/dep_id-4385/docid-200002522.html",
  ],
  bookingLinks: [],
  scheduleRowIds: [
    "4381_200002522_am",
    "4385_200002522_pm",
  ],
};
""",
    )

    assert result["ok"] is True
    assert result["target"] == {
        "unitId": "21",
        "depId": "4385",
        "doctorId": "200002522",
    }


def test_configured_target_mismatch_stops_resolution():
    result = _run_hook(
        "hooks.resolveTargetFromSnapshot(snapshot, { doctorId: 'other-doc' })",
        extra_js="""
const snapshot = {
  href: "https://www.91160.com/doctors/index/unit_id-21/dep_id-4385/docid-200002522.html",
  addMarkAttrs: null,
  doctorLinks: [],
  bookingLinks: [],
  scheduleRowIds: [],
};
""",
    )

    assert result["ok"] is False


def test_parse_schedule_payload_supports_direct_and_paiban_shapes():
    channel_1_payload = json.loads(
        (REPO_ROOT / "tests" / "fixtures" / "channel_1_schedule.json").read_text(
            encoding="utf-8"
        )
    )
    result = _run_hook(
        "[hooks.parseDoctorSchedulePayload(channel1, target), hooks.parseDoctorSchedulePayload(paiban, target)]",
        extra_js=f"""
const channel1 = {json.dumps(channel_1_payload, ensure_ascii=False)};
const target = {{ unitId: "131", depId: "369", doctorId: "200254692" }};
const paiban = {{
  code: 1,
  dates: {{ "2026-05-05": "二" }},
  sch: {{
    "group-1": {{
      "369_200254692_pm": {{
        "2026-05-05": {{
          schedule_id: "sch-live-1",
          doctor_id: "200254692",
          unit_id: "131",
          dep_id: "369",
          to_date: "2026-05-05",
          y_state: 1,
          dep_name: "康复医学科门诊",
        }},
      }},
    }},
  }},
}};
""",
    )

    direct_slots, paiban_slots = result
    assert direct_slots[0]["scheduleId"] == "sch-1001"
    assert paiban_slots[0]["scheduleId"] == "sch-live-1"
    assert paiban_slots[0]["weekday"] == 2
    assert paiban_slots[0]["dayPeriod"] == "pm"
    assert paiban_slots[0]["status"] == "available"


def test_filter_slots_keeps_coarse_slots_for_booking_page_hour_filter():
    result = _run_hook(
        """hooks.filterSlots(
  [
    { scheduleId: "sch-1", doctorId: "doc-1", weekday: 1, dayPeriod: "am", timeRange: "", status: "available" },
    { scheduleId: "sch-2", doctorId: "doc-1", weekday: 1, dayPeriod: "pm", timeRange: "14:00-14:30", status: "available" },
    { scheduleId: "sch-3", doctorId: "doc-2", weekday: 1, dayPeriod: "am", timeRange: "09:00-09:30", status: "available" }
  ],
  { doctorId: "doc-1" },
  { weeks: [1], days: [], hours: ["09:30-10:00"] }
)""",
    )

    assert [slot["scheduleId"] for slot in result] == ["sch-1"]


def test_appointment_attempt_key_is_schedule_and_appointment_not_schedule_only():
    result = _run_hook(
        """[
  hooks.appointmentKey("sch-1", "detl-1"),
  hooks.appointmentKey("sch-1", "detl-2"),
  hooks.appointmentKey("sch-1", null)
]""",
    )

    assert result == ["sch-1::detl-1", "sch-1::detl-2", "sch-1::<none>"]


def test_booking_form_hour_mismatch_does_not_depend_on_schedule_attempts():
    result = _run_hook(
        "hooks.parseBookingFormState({ hours: ['13:00-13:30'] }, 'sch-1001')",
        extra_js="""
const li1 = { getAttribute: (name) => name === "val" ? "detl-1" : "", textContent: "09:00-09:30" };
const li2 = { getAttribute: (name) => name === "val" ? "detl-2" : "", textContent: "09:30-10:00" };
const schedule = { value: "sch-1001" };
sandbox.document.querySelector = (selector) => selector === 'input[name="schedule_id"]' ? schedule : null;
sandbox.document.querySelectorAll = (selector) => selector === 'input[name="schedule_id"]' ? [schedule] : [];
const delts = { querySelectorAll: (selector) => selector === "li[val]" ? [li1, li2] : [] };
sandbox.document.querySelector = (selector) => {
  if (selector === 'input[name="schedule_id"]') return schedule;
  if (selector === "#delts") return delts;
  return null;
};
""",
    )

    assert result["isValid"] is False
    assert result["invalidReason"] == "hour_filter_mismatch"
    assert result["scheduleId"] == "sch-1001"


def test_old_generated_values_are_cleared_and_explicit_v4_values_survive():
    old = {
        "settingsVersion": 3,
        "address": {"province": "广东", "city": "深圳", "area": "南山区"},
        "booking": {"diseaseDescription": "门诊就诊，具体病情现场面诊沟通"},
    }
    migrated = _run_hook("hooks.normalizeSettings(" + json.dumps(old) + ")")
    assert migrated["settingsVersion"] == 4
    assert migrated["address"] == dict(province=None, city=None, area=None, detail=None)
    assert migrated["booking"]["diseaseDescription"] is None
    explicit = _run_hook(
        "hooks.normalizeSettings(" + json.dumps({**old, "settingsVersion": 4}) + ")"
    )
    assert explicit["address"]["province"] == "广东"
    assert (
        explicit["booking"]["diseaseDescription"]
        == old["booking"]["diseaseDescription"]
    )


def test_defaults_do_not_invent_clinical_or_address_values():
    settings = _run_hook("hooks.normalizeSettings(null)")
    assert settings["address"] == dict(province=None, city=None, area=None, detail=None)
    assert settings["booking"]["diseaseDescription"] is None
    assert settings["booking"]["clinicCard"] is None


def test_no_identity_response_parser_patch_is_installed():
    result = _run_hook(
        "({unchanged:sandbox.unsafeWindow.jQuery.ajax === originalAjax, patch:typeof hooks.installCheckIdInfoBlankResponsePatch})",
        extra_js="const originalAjax = () => ''; sandbox.unsafeWindow.jQuery = {ajax:originalAjax};",
    )
    assert result == {"unchanged": True, "patch": "undefined"}


def test_find_submit_control_does_not_click_before_trigger():
    result = _run_hook(
        """(() => {
  const control = hooks.findSubmitControl();
  const eventsBeforeTrigger = submitButton.events.slice();
  const submitResult = hooks.triggerSubmitControl(control);
  return {
    found: { method: control.method, target: control.target },
    eventsBeforeTrigger,
    eventsAfterTrigger: submitButton.events,
    submitResult,
  };
})()""",
        extra_js="""
const submitButton = {
  value: "提交订单",
  textContent: "",
  events: [],
  scrollIntoView() {
    this.events.push("scrollIntoView");
  },
  focus() {
    this.events.push("focus");
  },
  click() {
    this.events.push("native-click");
  },
  dispatchEvent(event) {
    this.events.push(event.type);
    return true;
  },
};
sandbox.document.querySelector = (selector) =>
  selector === "#suborder #submitbtn" ? submitButton : null;
sandbox.document.querySelectorAll = () => [submitButton];
""",
    )

    assert result["found"]["method"] == "selector"
    assert result["eventsBeforeTrigger"] == []
    assert result["eventsAfterTrigger"] == [
        "scrollIntoView",
        "focus",
        "native-click",
    ]
    assert result["submitResult"] == {
        "method": "selector",
        "target": result["found"]["target"],
        "activation": {
            "method": "native-click",
        },
    }


def test_mark_submit_in_progress_pauses_runner_before_navigation():
    result = _run_hook("""(async () => {
      hooks.writeState({ running: true, pendingBooking: { doctorId: "synthetic-doctor" } });
      const record = await hooks.markSubmitInProgress({ scheduleId: 'synthetic-slot', appointmentValue: 'synthetic-time' }, { memberId: 'SYN_MEMBER' }, { address: 'SYN_ADDRESS' }, 2);
      return { record, state: hooks.readState(), journal: hooks.readJournal() };
    })()""")
    assert result["state"]["running"] is False
    assert result["record"]["state"] == "SUBMITTING"
    assert result["state"]["outcome"] == "OUTCOME_UNKNOWN"
    assert "SYN_" not in json.dumps(result)


def test_inspect_booking_page_treats_navigation_away_as_unknown():
    result = _run_hook("hooks.inspectBookingPage('https://synthetic.invalid/before')")
    assert result["success"] is False
    assert result["state"] == "OUTCOME_UNKNOWN"


def test_logs_and_legacy_migration_drop_synthetic_sensitive_values():
    result = _run_hook("""(() => {
      const poison = "SYN_NAME_Q|SYN_CERT_Q|SYN_PHONE_Q|SYN_MEMBER_Q|SYN_TOKEN_Q|SYN_CARD_Q|SYN_ADDRESS_Q";
      const captured = [];
      sandbox.console.log = sandbox.console.warn = sandbox.console.error = (...args) => captured.push(args);
      sandbox.sessionStorage.setItem(hooks.STATE_KEY, JSON.stringify({
        running: true, pendingBooking: { scheduleId: "synthetic-slot" },
        logs: [{ ts: new Date().toISOString(), level: "info", message: poison, detail: poison }],
        summary: { message: poison, detail: poison },
      }));
      const migrated = hooks.readState();
      const storedMigration = JSON.parse(sandbox.sessionStorage.getItem(hooks.STATE_KEY));
      const entry = hooks.appendLog("info", poison, {
        memberId: poison, token: poison, selector: { value: poison },
        message: poison, url: "https://example.test/?token=" + poison,
        ready: true, count: 2, unknown: false,
      });
      return { entry, captured, migrated, storedMigration, state: hooks.readState() };
    })()""")
    assert "SYN_" not in json.dumps(result)
    assert result["migrated"]["logs"] == []
    assert result["storedMigration"]["summary"] is None
    assert result["migrated"]["pendingBooking"]["scheduleId"] == "synthetic-slot"
    assert json.loads(result["entry"]["detail"]) == {"ready": True, "count": 2}


def test_safe_workflow_summaries_preserve_manual_waiting_and_cooldown():
    result = _run_hook(
        """(() => {
      const poison = 'SYN_NAME_Q|SYN_CERT_Q|SYN_PHONE_Q|SYN_MEMBER_Q|SYN_TOKEN_Q|SYN_CARD_Q|SYN_ADDRESS_Q';
      hooks.writeState({ running: false, pendingBooking: null, submittingBooking: null });
      hooks.setSummary('info', 'Booking form prepared; waiting for manual submit.', {
        memberId: poison, address: { detail: poison }, appointmentLabel: poison,
      });
      const manual = hooks.readState();
      hooks.writeState({ running: true });
      hooks.setSummary('warn', 'Schedule polling hit rate limiting.', poison);
      const cooldown = hooks.readState();
      hooks.setSummary('info', 'Booking succeeded.', { url: poison });
      const success = hooks.readState();
      return {
        manual, manualPhase: hooks.panelPhase(manual),
        cooldown, cooldownPhase: hooks.panelPhase(cooldown), success,
        bookingRateLimit: hooks.panelPhase({ summary: hooks.appendLog('warn', 'Booking page hit rate limiting.', poison) }),
        invalid: hooks.panelPhase({ summary: hooks.appendLog('warn', 'Booking form invalid.', poison) }),
        failed: hooks.panelPhase({ summary: hooks.appendLog('warn', 'Booking submit failed.', poison) }),
      };
    })()""",
        extra_js="""
    const fakeBody = { innerHTML: '', querySelector: () => null };
    sandbox.document.getElementById = () => ({ querySelector: () => fakeBody });
    sandbox.console.log = sandbox.console.warn = sandbox.console.error = () => {};
    """,
    )
    assert result["manualPhase"] == "hit"
    assert (
        result["manual"]["summary"]["message"]
        == "Booking form prepared; waiting for manual submit."
    )
    assert result["cooldownPhase"] == "cooldown"
    assert result["bookingRateLimit"] == "cooldown"
    assert result["invalid"] == "error"
    assert result["failed"] == "error"
    assert (
        result["cooldown"]["summary"]["message"]
        == "Schedule polling hit rate limiting."
    )
    assert result["success"]["summary"]["message"] == "Booking succeeded."
    assert "SYN_" not in json.dumps(result)
