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
        pytest.skip("node is required to validate the userscript helpers")
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
  URL,
  Math,
  JSON,
  Date,
  Number,
  String,
  setTimeout,
  clearTimeout,
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
    assert "// @version      0.2.7" in content
    assert 'const SCRIPT_VERSION = "0.2.7";' in content
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
    assert "doctor=${htmlEscape(panelDoctorText(state))}" in content
    assert "Start Date" not in content
    assert "Appointment From" in content


def test_userscript_passes_node_syntax_check():
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("node is required to syntax-check the userscript")
    subprocess.run([node_bin, "--check", str(SCRIPT_PATH)], cwd=REPO_ROOT, check=True)


def test_normalize_settings_keeps_python_config_semantics_for_business_fields():
    result = _run_hook(
        """hooks.normalizeSettings({
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
    assert result["address"] == {
        "province": "广东",
        "city": "深圳",
        "area": "南山",
        "detail": "深南花园",
    }
    assert result["booking"]["autoSubmit"] is True
    assert result["booking"]["maxSubmitAttemptsPerAppointment"] == 4
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


def test_controller_claim_is_singleton_per_page_instance():
    result = _run_hook(
        """(() => {
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
})()""",
    )

    assert result["firstIsActive"] is True
    assert result["second"] is None
    assert result["sameController"] is True
    assert result["stateControllerId"].startswith("doctor:")


def test_manual_start_restarts_active_controller():
    result = _run_hook(
        """(() => {
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
})()""",
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
sandbox.document.querySelectorAll = () => [];
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


def test_resolve_member_selection_autoselects_single_candidate():
    result = _run_hook(
        "hooks.resolveMemberSelection({ memberId: null, memberLabel: null })",
        extra_js="""
const memberRadio = {
  value: "147750901",
  getAttribute: (n) => (n === "name" ? "mid" : ""),
  checked: false,
  disabled: false,
  parentElement: { textContent: "吴非 男" },
  closest: (selector) => selector.includes('tr[id^="mem"]') ? { id: "mem147750901" } : null,
};
sandbox.document.querySelectorAll = (selector) =>
  selector === 'input[type="radio"]' ? [memberRadio] : [];
""",
    )

    assert result["ok"] is True
    assert result["memberId"] == "147750901"


def test_resolve_member_selection_requires_config_when_multiple_candidates():
    result = _run_hook(
        "hooks.resolveMemberSelection({ memberId: null, memberLabel: null })",
        extra_js="""
const radio = (value, text) => ({
  value,
  getAttribute: (n) => (n === "name" ? "mid" : ""),
  checked: false,
  disabled: false,
  parentElement: { textContent: text },
  closest: (selector) => selector.includes('tr[id^="mem"]') ? { id: `mem${value}` } : null,
});
sandbox.document.querySelectorAll = (selector) =>
  selector === 'input[type="radio"]' ? [radio("1", "张三"), radio("2", "李四")] : [];
""",
    )

    assert result["ok"] is False
    assert "Multiple member candidates" in result["reason"]


def test_fill_booking_form_uses_configured_disease_description():
    result = _run_hook(
        """(() => {
  const fillResult = hooks.fillBookingForm(
    { appointmentValue: null, appointmentOptions: [] },
    { memberId: "147750901", radio: null },
    {},
    { diseaseDescription: "门诊就诊，具体病情现场面诊沟通" }
  );
  return {
    fillResult,
    diseaseInputValue: diseaseInput.value,
    diseaseContentValue: diseaseContent.value,
    memberValue: memberInput.value,
  };
})()""",
        extra_js="""
const memberInput = { value: "" };
const diseaseInput = { value: "" };
const diseaseContent = { value: "" };
const elements = {
  'input[name="member_id"]': memberInput,
  'input[name="disease_input"]': diseaseInput,
  'textarea[name="disease_content"]': diseaseContent,
};
sandbox.document.querySelector = (selector) => elements[selector] ?? null;
""",
    )

    assert result["fillResult"]["addressSelection"]["ok"] is True
    assert result["fillResult"]["clinicIdSelection"]["required"] is False
    assert result["fillResult"]["scheduleDateSelection"]["required"] is False
    assert result["memberValue"] == "147750901"
    assert result["diseaseInputValue"] == "门诊就诊，具体病情现场面诊沟通"
    assert result["diseaseContentValue"] == "门诊就诊，具体病情现场面诊沟通"


def test_fill_booking_form_sets_schedule_date_from_serialized_schedule_data():
    result = _run_hook(
        """(() => {
  const fillResult = hooks.fillBookingForm(
    { scheduleId: "sch-live-1", appointmentValue: null, appointmentOptions: [] },
    { memberId: "147750901", radio: null },
    {},
    { diseaseDescription: "门诊就诊，具体病情现场面诊沟通" }
  );
  return {
    fillResult,
    schDateValue: schDate.value,
    schDateEvents: schDate.events,
  };
})()""",
        extra_js="""
sandbox.Event = class Event {
  constructor(type) {
    this.type = type;
  }
};
const schDate = {
  value: "",
  events: [],
  dispatchEvent(event) {
    this.events.push(event.type);
  },
};
const schData = {
  value: 'a:1:{s:10:"sch-live-1";a:1:{s:3:"sch";a:1:{s:7:"to_date";s:10:"2026-07-10";}}}',
};
const memberInput = { value: "" };
const diseaseInput = { value: "" };
const diseaseContent = { value: "" };
const elements = {
  "#sch_date": schDate,
  'input[name="sch_date"]': schDate,
  'input[name="sch_data"]': schData,
  'input[name="member_id"]': memberInput,
  'input[name="disease_input"]': diseaseInput,
  'textarea[name="disease_content"]': diseaseContent,
};
sandbox.document.querySelector = (selector) => elements[selector] ?? null;
""",
    )

    assert result["fillResult"]["scheduleDateSelection"] == {
        "ok": True,
        "required": True,
        "filled": True,
        "source": "schedule",
        "value": "2026-07-10",
    }
    assert result["schDateValue"] == "2026-07-10"
    assert result["schDateEvents"] == ["input", "change"]


def test_fill_address_selection_cascades_configured_region():
    result = _run_hook(
        """(() => {
  const addressSelection = hooks.fillAddressSelection(
    { province: "广东", city: "深圳", area: "南山", detail: "深南花园" },
    { radio: null }
  );
  return {
    addressSelection,
    provinceValue: province.value,
    cityValue: city.value,
    areaValue: area.value,
    detailValue: detail.value,
    provinceEvents: province.events,
    cityEvents: city.events,
  };
})()""",
        extra_js="""
sandbox.Event = class Event {
  constructor(type) {
    this.type = type;
  }
};
const option = (value, text) => ({ value, text, textContent: text, selected: false });
const makeSelect = (id, options) => ({
  id,
  options,
  value: options[0].value,
  selectedIndex: 0,
  events: [],
  dispatchEvent(event) {
    this.events.push(event.type);
    if (event.type !== "change") return;
    if (id === "useraddress_province" && this.value === "2") {
      city.options = [option("0", "选择市"), option("5", "深圳")];
      city.value = "0";
      city.selectedIndex = 0;
    }
    if (id === "useraddress_city" && this.value === "5") {
      area.options = [option("0", "选择区"), option("8", "南山区")];
      area.value = "0";
      area.selectedIndex = 0;
    }
  },
});
const province = makeSelect("useraddress_province", [
  option("0", "请选择"),
  option("2", "广东"),
]);
const city = makeSelect("useraddress_city", [option("0", "请选择")]);
const area = makeSelect("useraddress_area", [option("0", "请选择")]);
const detail = { value: "", events: [], dispatchEvent(event) { this.events.push(event.type); } };
const elements = {
  "#useraddress_province": province,
  "#useraddress_city": city,
  "#useraddress_area": area,
  "#useraddress_detail": detail,
};
sandbox.document.querySelector = (selector) => elements[selector] ?? null;
""",
    )

    assert result["addressSelection"]["ok"] is True
    assert result["provinceValue"] == "2"
    assert result["cityValue"] == "5"
    assert result["areaValue"] == "8"
    assert result["detailValue"] == "深南花园"
    assert result["provinceEvents"] == ["input", "change"]
    assert result["cityEvents"] == ["input", "change"]


def test_fill_address_selection_uses_member_region_ids_before_config():
    result = _run_hook(
        """(() => {
  const radio = {
    getAttribute(name) {
      return {
        province_id: "2",
        city_id: "5",
        area_id: "8",
        address: "member detail",
      }[name] ?? "";
    },
  };
  const addressSelection = hooks.fillAddressSelection(
    { province: "湖南", city: "长沙", area: "岳麓" },
    { radio }
  );
  return {
    addressSelection,
    values: [province.value, city.value, area.value],
    detailValue: detail.value,
  };
})()""",
        extra_js="""
sandbox.Event = class Event {
  constructor(type) {
    this.type = type;
  }
};
const option = (value, text) => ({ value, text, textContent: text, selected: false });
const makeSelect = (id, options) => ({
  id,
  options,
  value: options[0].value,
  selectedIndex: 0,
  dispatchEvent(event) {
    if (event.type !== "change") return;
    if (id === "useraddress_province" && this.value === "2") {
      city.options = [option("0", "选择市"), option("5", "深圳")];
      city.value = "0";
      city.selectedIndex = 0;
    }
    if (id === "useraddress_city" && this.value === "5") {
      area.options = [option("0", "选择区"), option("8", "南山区")];
      area.value = "0";
      area.selectedIndex = 0;
    }
  },
});
const province = makeSelect("useraddress_province", [
  option("0", "请选择"),
  option("2", "广东"),
  option("3260", "湖南"),
]);
const city = makeSelect("useraddress_city", [option("0", "请选择")]);
const area = makeSelect("useraddress_area", [option("0", "请选择")]);
const detail = { value: "", dispatchEvent() {} };
const elements = {
  "#useraddress_province": province,
  "#useraddress_city": city,
  "#useraddress_area": area,
  "#useraddress_detail": detail,
};
sandbox.document.querySelector = (selector) => elements[selector] ?? null;
""",
    )

    assert result["addressSelection"]["ok"] is True
    assert result["values"] == ["2", "5", "8"]
    assert result["detailValue"] == "member detail"


def test_fill_address_selection_fails_when_required_region_is_missing():
    result = _run_hook(
        "hooks.fillAddressSelection({ province: null, city: null, area: null }, { radio: null })",
        extra_js="""
const option = (value, text) => ({ value, text, textContent: text, selected: false });
const province = {
  options: [option("0", "请选择"), option("2", "广东")],
  value: "0",
  selectedIndex: 0,
  dispatchEvent() {},
};
sandbox.document.querySelector = (selector) =>
  selector === "#useraddress_province" ? province : null;
""",
    )

    assert result["ok"] is False
    assert "Missing address province" in result["reason"]


def test_fill_clinic_id_uses_member_real_card_without_returning_value():
    result = _run_hook(
        """(() => {
  const clinicId = hooks.fillClinicId({ radio });
  const submitValue = input.attrs.true_value || "";
  return {
    clinicId,
    inputValueLength: input.value.length,
    inputWasFilled: input.value === "11010519491231002X",
    submitValueLength: submitValue.length,
    submitValueMatchesInput: submitValue === input.value,
    events: input.events,
  };
})()""",
        extra_js="""
sandbox.Event = class Event {
  constructor(type) {
    this.type = type;
  }
};
const input = {
  value: "",
  attrs: {},
  events: [],
  setAttribute(name, value) {
    this.attrs[name] = value;
  },
  dispatchEvent(event) {
    this.events.push(event.type);
  },
};
const radio = {
  getAttribute(name) {
    return name === "real_card" ? "11010519491231002X" : "";
  },
};
sandbox.document.querySelector = (selector) =>
  selector === "#hismemid" ? input : null;
""",
    )

    assert result["clinicId"] == {
        "ok": True,
        "required": True,
        "filled": True,
        "source": "member-radio:real_card",
        "valueLength": 18,
    }
    assert result["inputValueLength"] == 18
    assert result["inputWasFilled"] is True
    assert result["submitValueLength"] == 18
    assert result["submitValueMatchesInput"] is True
    assert result["events"] == ["input", "change", "blur"]


def test_fill_clinic_id_keeps_existing_value():
    result = _run_hook(
        """(() => {
  const clinicId = hooks.fillClinicId({ radio });
  return { clinicId, inputValue: input.value, trueValue: input.attrs.true_value, events: input.events };
})()""",
        extra_js="""
const input = {
  value: "existing-card",
  attrs: {},
  events: [],
  getAttribute(name) {
    return this.attrs[name] || "";
  },
  setAttribute(name, value) {
    this.attrs[name] = value;
  },
  dispatchEvent(event) {
    this.events.push(event.type);
  },
};
const radio = {
  getAttribute(name) {
    return name === "real_card" ? "11010519491231002X" : "";
  },
};
sandbox.document.querySelector = (selector) =>
  selector === "#hismemid" ? input : null;
""",
    )

    assert result["clinicId"] == {
        "ok": True,
        "required": True,
        "filled": False,
        "source": "existing",
        "valueLength": 13,
    }
    assert result["inputValue"] == "existing-card"
    assert result["trueValue"] == "existing-card"
    assert result["events"] == []


def test_checkidinfo_blank_response_patch_only_normalizes_target_json():
    result = _run_hook(
        """(() => {
  const firstInstall = hooks.installCheckIdInfoBlankResponsePatch();
  const objectFormBlank = sandbox.unsafeWindow.jQuery.ajax({
    url: "/guahao/checkidinfo.html",
    dataType: "json",
  });
  const stringFormBlank = sandbox.unsafeWindow.jQuery.ajax(
    "/guahao/checkIdInfo.html",
    { dataType: "json" }
  );
  const objectFormNonBlank = sandbox.unsafeWindow.jQuery.ajax({
    url: "/guahao/checkidinfo.html",
    dataType: "json",
    simulatedResponse: "{\\\"code\\\":0}",
  });
  const otherRequest = sandbox.unsafeWindow.jQuery.ajax({
    url: "/guahao/other.html",
    dataType: "json",
  });
  const secondInstall = hooks.installCheckIdInfoBlankResponsePatch();
  return {
    firstInstall,
    objectFormBlank,
    stringFormBlank,
    objectFormNonBlank,
    otherRequest,
    secondInstall,
    calls,
  };
})()""",
        extra_js="""
const calls = [];
const originalAjax = function ajax(...args) {
  const options = typeof args[0] === "string" ? args[1] : args[0];
  const url = typeof args[0] === "string" ? args[0] : options.url;
  const raw = options.simulatedResponse ?? " \\n ";
  const filtered = typeof options.dataFilter === "function"
    ? options.dataFilter(raw, options.dataType)
    : raw;
  calls.push({ url, filtered });
  return filtered;
};
sandbox.unsafeWindow = { jQuery: { ajax: originalAjax } };
""",
    )

    assert result["firstInstall"] == {"installed": True}
    assert result["objectFormBlank"] == "{}"
    assert result["stringFormBlank"] == "{}"
    assert result["objectFormNonBlank"] == '{"code":0}'
    assert result["otherRequest"] == " \n "
    assert result["secondInstall"] == {"installed": False, "alreadyInstalled": True}
    assert result["calls"] == [
        {"url": "/guahao/checkidinfo.html", "filtered": "{}"},
        {"url": "/guahao/checkIdInfo.html", "filtered": "{}"},
        {"url": "/guahao/checkidinfo.html", "filtered": '{"code":0}'},
        {"url": "/guahao/other.html", "filtered": " \n "},
    ]


def test_normalize_checkidinfo_response_keeps_non_blank_and_non_json_values():
    result = _run_hook(
        """[
  hooks.normalizeCheckIdInfoJsonResponse(" \\n ", "json"),
  hooks.normalizeCheckIdInfoJsonResponse("0", "json"),
  hooks.normalizeCheckIdInfoJsonResponse("", "text"),
  hooks.normalizeCheckIdInfoJsonResponse({ code: 1 }, "json")
]""",
    )

    assert result == ["{}", "0", "", {"code": 1}]


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
  dispatchEvent(event) {
    this.events.push(event.type);
  },
};
sandbox.document.querySelector = (selector) =>
  selector === "#suborder #submitbtn" ? submitButton : null;
sandbox.document.querySelectorAll = () => [];
""",
    )

    assert result["found"] == {"method": "selector", "target": "#suborder #submitbtn"}
    assert result["eventsBeforeTrigger"] == []
    assert result["eventsAfterTrigger"] == ["click"]
    assert result["submitResult"] == {"method": "selector", "target": "#suborder #submitbtn"}


def test_mark_submit_in_progress_pauses_runner_before_navigation():
    result = _run_hook(
        """(() => {
  sandbox.sessionStorage.setItem(hooks.STATE_KEY, JSON.stringify({
    running: true,
    controllerId: "booking:active",
    pendingBooking: { doctorId: "14707" },
    submitAttempts: { "sch-1::detl-1": 1 },
  }));
  const detail = hooks.markSubmitInProgress(
    {
      scheduleId: "sch-1",
      appointmentValue: "detl-1",
      appointmentLabel: "08:00-08:30",
    },
    { memberId: "147750901" },
    { addressSelection: { ok: true, area: { value: "8", text: "南山区" } } },
    2
  );
  const state = JSON.parse(sandbox.sessionStorage.getItem(hooks.STATE_KEY));
  return {
    detail,
    running: state.running,
    controllerId: state.controllerId,
    pendingBooking: state.pendingBooking,
    submittingBooking: state.submittingBooking,
    summary: state.summary,
  };
})()""",
        extra_js="""
const fakeBody = {
  innerHTML: "",
  querySelector: () => null,
};
const fakePanel = {
  querySelector: (selector) => (selector === ".grab160-body" ? fakeBody : null),
};
sandbox.document.getElementById = () => fakePanel;
sandbox.console.log = () => {};
sandbox.console.warn = () => {};
sandbox.console.error = () => {};
""",
    )

    assert result["running"] is False
    assert result["controllerId"] is None
    assert result["pendingBooking"] is None
    assert result["submittingBooking"]["scheduleId"] == "sch-1"
    assert result["submittingBooking"]["appointmentValue"] == "detl-1"
    assert result["submittingBooking"]["attemptCount"] == 2
    assert result["summary"]["message"] == (
        "Submitted booking form; runner paused to avoid duplicate submit."
    )


def test_inspect_booking_page_treats_navigation_away_as_success():
    result = _run_hook(
        "hooks.inspectBookingPage(beforeUrl)",
        extra_js="""
const beforeUrl = "https://www.91160.com/guahao/ystep1/uid-u/depid-d/schid-sch.html";
sandbox.location = new URL("https://www.91160.com/guahao/success.html");
sandbox.document.querySelector = () => null;
sandbox.document.querySelectorAll = () => [];
""",
    )

    assert result["success"] is True
