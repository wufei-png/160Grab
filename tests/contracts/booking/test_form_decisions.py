import json
from pathlib import Path

import pytest

from grab.booking.form import decide, snapshot_html

BASE = """<form id="suborder"><input name="schedule_id" value="synthetic-slot">
<input type="radio" name="mid" value="synthetic-member">
<ul id="delts"><li val="synthetic-time">09:00-09:30</li></ul>
<button id="submitbtn">Submit</button>{fields}</form>"""
SELECTION = dict(
    member_id="synthetic-member",
    schedule_id="synthetic-slot",
    appointment_value="synthetic-time",
    date="2030-01-02",
)


def test_snapshot_handles_attribute_order_single_quotes_and_entities():
    snapshot = snapshot_html(
        BASE.format(
            fields="<textarea required name='disease_input'>synthetic &amp; existing</textarea>"
        )
    )
    assert snapshot["fields"]["disease_input"][0]["value"] == "synthetic & existing"
    assert decide(snapshot, SELECTION)["state"] == "PREPARED"


def test_conflicting_existing_values_are_preserved_without_any_click_proposal():
    snapshot = snapshot_html(
        BASE.format(fields='<input name="disease_input" value="synthetic-existing">')
    )
    result = decide(snapshot, SELECTION, {"disease_input": "synthetic-config"})
    assert result["blockers"] == ["disease_input.conflict"]
    assert result["writes"] == []
    assert not result["can_prepare"]


def test_missing_required_fields_do_not_get_fabricated_values():
    snapshot = snapshot_html(
        BASE.format(
            fields='<input id="hismemid"><textarea name="disease_input"></textarea><input id="sch_date"><input name="address">'
        )
    )
    result = decide(snapshot, {**SELECTION, "date": None})
    assert result["blockers"] == [
        "address.detail.required",
        "card.required",
        "date.required",
        "disease_input.required",
    ]
    assert result["writes"] == []


def test_rules_and_unknown_required_controls_require_manual_action():
    snapshot = snapshot_html(
        BASE.format(
            fields='<input type="checkbox" name="accept" value="1"><input required name="synthetic_extra">'
        )
    )
    result = decide(snapshot, SELECTION)
    assert result["blockers"] == ["accept.required", "other.required"]


def test_single_wrong_or_duplicate_member_never_prepare():
    for html in [
        BASE.replace('value="synthetic-member"', 'value="synthetic-wrong"'),
        BASE.replace(
            "<ul", '<input type="radio" name="mid" value="synthetic-member"><ul'
        ),
    ]:
        result = decide(snapshot_html(html.format(fields="")), SELECTION)
        assert "member.mismatch" in result["blockers"]
        assert not result["can_prepare"]


PREPARATION = json.loads(
    (Path(__file__).parent / "fixtures/preparation.v1.json").read_text()
)["scenarios"]


@pytest.mark.parametrize("case", PREPARATION, ids=lambda c: c["id"])
def test_shared_preparation_decisions(case):
    snapshot = snapshot_html(case["html"])
    result = decide(snapshot, case["selection"], case["values"])
    assert {k: result[k] for k in case["expected"] if k != "submit_clicks"} == {
        k: v for k, v in case["expected"].items() if k != "submit_clicks"
    }
