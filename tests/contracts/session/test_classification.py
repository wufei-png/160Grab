import json
from pathlib import Path

from grab.services.session_state import classify_schedule
from tests.userscripts.test_doctor_page_poller import _run_hook


def test_python_userscript_share_session_scenarios():
    cases = json.loads(Path(__file__).with_name("scenarios.v1.json").read_text())[
        "cases"
    ]
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
