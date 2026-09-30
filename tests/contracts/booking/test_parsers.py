import json
import shutil
import subprocess
from pathlib import Path

import pytest

from grab.models.schemas import GrabConfig
from grab.services.booking import PageBookingStrategy
from tests.contracts.booking.scenarios import (
    SCENARIOS,
    USERSCRIPT,
    SyntheticDOM,
    read_html,
)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario["id"])
def test_python_booking_parser_contract(scenario):
    strategy = PageBookingStrategy(
        page=None, config=GrabConfig(hours=scenario["filters"]["hours"])
    )
    result = strategy.parse_booking_form(
        read_html(scenario), member_id=scenario["selection"]["member_id"]
    )
    expected = scenario["expected"]["parser"]
    assert result.model_dump(include=set(expected)) == expected
    assert result.member_id == scenario["selection"]["member_id"]


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario["id"])
def test_node_booking_parser_contract(scenario):
    node = shutil.which("node")
    assert node is not None, "Node is required for booking contract acceptance"
    dom = SyntheticDOM(read_html(scenario))
    completed = subprocess.run(
        [node, str(Path(__file__).with_name("node_harness.cjs")), str(USERSCRIPT)],
        input=json.dumps(
            {
                "schedule_id": dom.schedule_id,
                "options": dom.options,
                "hours": scenario["filters"]["hours"],
            }
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    result = json.loads(completed.stdout)
    assert result["parser"] == scenario["expected"]["parser"]
    assert result["click_count"] == scenario["expected"]["click_count"]


def test_fixture_inputs_are_only_synthetic_and_have_no_active_content():
    for scenario in SCENARIOS:
        html = read_html(scenario)
        dom = SyntheticDOM(html)
        assert scenario["scope"] == "parse-only"
        assert scenario["expected"]["state"] == "DISCOVERED"
        assert scenario["expected"]["click_count"] == 0
        assert dom.inputs == {
            **({"schedule_id": "synthetic-schedule-a"} if dom.schedule_id else {}),
            "member_id": "synthetic-member-a",
            "sch_date": "2030-01-02",
        }
        assert dom.submit_candidates == scenario["submit_candidate_count"]
        assert "<script" not in html.lower()
        assert "https://" not in html.lower()
