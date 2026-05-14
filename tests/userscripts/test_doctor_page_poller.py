import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "userscripts" / "91160-doctor-page-poller.user.js"


def _run_node_script(js_code: str) -> str:
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("node is required to validate the userscript helpers")
    completed = subprocess.run(
        [node_bin, "-e", js_code],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
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
  globalThis: null,
  window: null,
  self: null,
  unsafeWindow: null,
  location: new URL("https://www.91160.com/doctors/index/unit_id-21/dep_id-0/docid-200002522.html"),
  document: {{
    querySelector: () => null,
    querySelectorAll: () => [],
    getElementById: () => null,
    body: {{ innerText: "", textContent: "" }},
    documentElement: {{ appendChild() {{}}, outerHTML: "", innerText: "", textContent: "" }},
  }},
  sessionStorage: {{
    getItem: () => null,
    setItem: () => null,
    removeItem: () => null,
  }},
  setTimeout,
  clearTimeout,
  fetch: async () => ({{ text: async () => "{{}}", status: 200 }}),
  getComputedStyle: () => ({{ display: "block", visibility: "visible" }}),
  MouseEvent: class MouseEvent {{}},
  Event: class Event {{}},
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
const result = {expression};
process.stdout.write(JSON.stringify(result));
"""
    output = _run_node_script(code)
    return json.loads(output)


def test_userscript_file_exists_and_has_expected_matches():
    content = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "@match        https://www.91160.com/doctors/index/*" in content
    assert "@match        https://www.91160.com/guahao/ystep1/*" in content
    assert "const CONFIG = {" in content
    assert "jQuery?.ajax" in content


def test_userscript_passes_node_syntax_check():
    node_bin = shutil.which("node")
    if node_bin is None:
        pytest.skip("node is required to syntax-check the userscript")
    subprocess.run([node_bin, "--check", str(SCRIPT_PATH)], cwd=REPO_ROOT, check=True)


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
    assert direct_slots[0]["status"] == "available"
    assert paiban_slots[0]["scheduleId"] == "sch-live-1"
    assert paiban_slots[0]["weekday"] == 2
    assert paiban_slots[0]["dayPeriod"] == "pm"
    assert paiban_slots[0]["status"] == "available"


def test_hour_filter_and_rate_limit_helpers_match_repo_behavior():
    result = _run_hook(
        """({
  normalizedHour: hooks.normalizeHourValue("9.5-10"),
  filteredCoarse: hooks.filterSlots(
    [{ scheduleId: "sch-1", doctorId: "doc-1", weekday: 1, dayPeriod: "am", timeRange: "", status: "available" }],
    { doctorId: "doc-1" },
    { weeks: [], days: [], hours: ["09:30-10:00"] }
  ),
  appointment: hooks.chooseAppointmentOption(
    [
      { value: "detl-1", label: "09:00-09:30" },
      { value: "detl-2", label: "09:30-10:00" }
    ],
    { hours: ["09:30-10:00"] }
  ),
  rateLimit: hooks.extractRateLimitMessage({ message: "您单位时间内访问次数过多，请稍后再试" }),
})""",
    )

    assert result["normalizedHour"] == "09:30-10:00"
    assert len(result["filteredCoarse"]) == 1
    assert result["appointment"]["value"] == "detl-2"
    assert "访问次数过多" in result["rateLimit"]


def test_panel_position_helpers_normalize_and_clamp():
    result = _run_hook(
        """({
  normalized: hooks.normalizePanelPosition({ left: "12.7", top: 45 }),
  invalid: hooks.normalizePanelPosition({ left: "abc", top: 45 }),
  clamped: hooks.clampPanelPosition(
    { left: -20, top: 900 },
    { width: 1280, height: 720 },
    { width: 320, height: 120 }
  ),
})""",
    )

    assert result["normalized"] == {"left": 13, "top": 45}
    assert result["invalid"] is None
    assert result["clamped"] == {"left": 8, "top": 592}


def test_member_radio_debug_summary_separates_member_and_non_member_radios():
    result = _run_hook(
        "hooks.memberRadioDebugSummary(memberRadios, ignoredRadios)",
        extra_js="""
const formStub = { id: "bookForm", getAttribute: () => null, className: "main" };
const memberRadios = [
  {
    value: "  1001  ",
    getAttribute: (n) => (n === "name" ? "mid" : n === "id" ? "" : null),
    checked: true,
    disabled: false,
    form: formStub,
    parentElement: { textContent: "  张三  男  " },
    closest: () => null,
  },
];
const ignoredRadios = [
  {
    value: "pay_online",
    getAttribute: (n) => (n === "name" ? "pay_type" : ""),
    checked: false,
    disabled: false,
    form: formStub,
    parentElement: { textContent: "在线支付" },
    closest: () => null,
  },
];
""",
    )

    assert "Found 1 candidate member radio(s) out of 2 total" in result
    assert "member#0" in result
    assert "ignored#0" in result
    assert "1001" in result and "pay_online" in result
    assert "张三" in result


def test_find_member_radios_filters_out_payment_and_gender_radios():
    result = _run_hook(
        """hooks.findMemberRadios().map((radio) => ({
  value: radio.value,
  name: radio.getAttribute("name"),
}))""",
        extra_js="""
const radios = [
  {
    value: "147750901",
    getAttribute: (n) => (n === "name" ? "mid" : n === "id" ? "" : null),
    checked: false,
    disabled: false,
    form: { id: "suborder", getAttribute: () => null, className: "" },
    parentElement: { textContent: "吴非 男 身份证 412***********0336" },
    closest: (selector) => (selector.includes('tr[id^="mem"]') ? { id: "mem147750901" } : null),
  },
  {
    value: "1",
    getAttribute: (n) => (n === "name" ? "pay_online" : ""),
    checked: true,
    disabled: false,
    form: { id: "suborder", getAttribute: () => null, className: "" },
    parentElement: { textContent: "在线支付" },
    closest: () => null,
  },
  {
    value: "0",
    getAttribute: (n) => (n === "name" ? "jzr_sex" : n === "id" ? "jzr_sex_male" : ""),
    checked: false,
    disabled: false,
    form: null,
    parentElement: { textContent: "男" },
    closest: () => null,
  },
];
sandbox.document.querySelectorAll = (selector) =>
  selector === 'input[type="radio"]' ? radios : [];
""",
    )

    assert result == [{"value": "147750901", "name": "mid"}]


def test_resolve_member_selection_allows_hidden_member_id_with_only_ignored_radios():
    result = _run_hook(
        "hooks.resolveMemberSelection({ memberId: '147750901', memberLabel: null })",
        extra_js="""
const payRadio = {
  value: "1",
  getAttribute: (n) => (n === "name" ? "pay_online" : ""),
  checked: true,
  disabled: false,
  form: { id: "suborder", getAttribute: () => null, className: "" },
  parentElement: { textContent: "在线支付" },
  closest: () => null,
};
const hiddenMemberId = { value: "147750901" };
sandbox.document.querySelectorAll = (selector) =>
  selector === 'input[type="radio"]' ? [payRadio] : [];
sandbox.document.querySelector = (selector) => {
  if (selector === 'input[name="member_id"]') return hiddenMemberId;
  return null;
};
""",
    )

    assert result["ok"] is True
    assert result["memberId"] == "147750901"
    assert result["radio"] is None
