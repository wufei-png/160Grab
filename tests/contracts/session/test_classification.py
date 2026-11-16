import json
from pathlib import Path

import pytest

from grab.models.schemas import DoctorPageTarget
from grab.services.schedule import ScheduleService
from grab.services.session_state import classify_schedule
from tests.userscripts.test_doctor_page_poller import _run_hook


def test_python_userscript_share_session_scenarios():
    cases = json.loads(
        Path(__file__).with_name("scenarios.v1.json").read_text(encoding="utf-8")
    )["cases"]
    js = _run_hook(
        "cases.map(c => hooks.classifySchedule(c.payload))",
        "const cases = " + json.dumps(cases) + ";",
    )
    for case, result in zip(cases, js, strict=True):
        python = classify_schedule(case["payload"])
        assert python.state == result["state"] == case["state"]
        assert (
            python.failure_class == result.get("failureClass") == case["failure_class"]
        )


@pytest.mark.parametrize(
    "value", ["1", "0", "-1", "-2", "-3", "01", "1.0", "1 ", True, None]
)
def test_provider_status_string_enum_parity(value):
    payload = {
        "code": 1,
        "sch": {"synthetic": {"2030-01-02": {"schedule_id": "slot", "y_state": value}}},
        "dates": {},
    }
    expected = (
        "VALID"
        if type(value) is str and value in {"1", "0", "-1", "-2", "-3"}
        else "UNKNOWN"
    )
    js = _run_hook(
        "hooks.classifySchedule(payload)",
        "const payload = " + json.dumps(payload) + ";",
    )
    assert classify_schedule(payload).state == js["state"] == expected
    if expected == "VALID":
        service = ScheduleService(None)
        service.set_target(
            DoctorPageTarget(unit_id="u", dept_id="d", doctor_id="doc", source_url="")
        )
        python_status = service.parse_doctor_schedule(payload)[0].status
        js_status = _run_hook(
            "hooks.parseDoctorSchedulePayload(payload,{unitId:'u',depId:'d',doctorId:'doc'})[0].status",
            "const payload = " + json.dumps(payload) + ";",
        )
        assert (
            python_status
            == js_status
            == {
                "1": "available",
                "0": "full",
                "-1": "expired",
                "-2": "stopped",
                "-3": "not_open",
            }[value]
        )
