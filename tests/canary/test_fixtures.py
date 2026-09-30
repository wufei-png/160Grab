import json
import subprocess

import pytest

from grab.booking.form import decide, snapshot_html
from grab.canary.fixtures import (
    FixtureRejected,
    convert_booking_html,
    convert_schedule,
    fingerprint,
)

META = dict(
    captured_on="2026-09-30", level="prepare", path="python", source="synthetic"
)
SECRET = "SYN_PRIVATE_NAME_PHONE_MEMBER_CARD_ADDRESS_TOKEN"


def test_minimal_dom_rebuild_removes_all_raw_sinks_preserves_decision():
    raw = f'''<script>window.token="{SECRET}";</script>
      <script type="application/json">{{"secret":"{SECRET}"}}</script>
      <a href="https://private.invalid/?token={SECRET}" onclick="alert('{SECRET}')">{SECRET}</a>
      <form id="suborder" action="/?token={SECRET}">
      <input name="schedule_id" value="{SECRET}-slot">
      <label>{SECRET}<input type="radio" name="mid" value="{SECRET}-member" checked address="{SECRET}"></label>
      <ul id="delts"><li val="{SECRET}-time" class="selected">09:00-09:30 {SECRET}</li></ul>
      <input id="sch_date" value="2026-10-01">
      <input id="hismemid" value="{SECRET}"><textarea name="disease_input">{SECRET}</textarea>
      <select id="useraddress_area"><option value="{SECRET}" selected>{SECRET}</option></select>
      <input name="csrf" value="{SECRET}"><button id="submitbtn">{SECRET}</button></form>'''
    document = convert_booking_html(raw, **META)
    encoded = json.dumps(document)
    assert SECRET not in encoded and "private.invalid" not in encoded
    assert "<script" not in document["html"] and "onclick" not in document["html"]
    snapshot = document["snapshot"]
    regenerated = snapshot_html(document["html"])
    selection = dict(
        member_id=snapshot["members"][0]["ids"][0],
        schedule_id=snapshot["schedule_ids"][0],
        appointment_value=snapshot["times"][0]["value"],
        date=snapshot["fields"]["date"][0]["value"],
    )
    assert decide(regenerated, selection) == decide(snapshot, selection)
    assert document["metadata"]["schema_fingerprint"] == fingerprint(snapshot)
    assert document["metadata"]["source"] == "synthetic"
    assert document["metadata"]["coverage"]["scripts_removed"] == 2


@pytest.mark.parametrize(
    "control",
    [
        '<input name="unrecognized_secret" value="SYN_SECRET">',
        '<textarea id="unrecognized_secret">SYN_SECRET</textarea>',
        '<select name="unknown"><option>SYN_SECRET</option></select>',
    ],
)
def test_unknown_form_fields_fail_closed(control):
    with pytest.raises(FixtureRejected, match="unsupported schema"):
        convert_booking_html(control, **META)


def test_missing_disabled_double_controls_and_conflicting_dates_preserved():
    raw = """<input name="schedule_id" value="a"><input name="schedule_id" value="b">
        <input type="hidden" name="mid" value="M">
        <div><span id="jzdate">2026年09月30日</span></div><input id="sch_date" value="2026-10-01">
        <input id="hismemid" value="" disabled>
        <button id="submitbtn" disabled>预约</button><button id="submit_booking">预约</button>"""
    document = convert_booking_html(raw, **META)
    snapshot = document["snapshot"]
    assert snapshot["submit"] == [False, True]
    assert snapshot["fields"]["card"][0]["value"] == ""
    assert snapshot["dates"][0] != snapshot["fields"]["date"][0]["value"]
    selection = dict(
        member_id="synthetic-member-1",
        schedule_id="synthetic-schedule-1",
        date=snapshot["dates"][0],
    )
    assert decide(snapshot_html(document["html"]), selection) == decide(
        snapshot, selection
    )


def test_schedule_schema_values_sanitized_and_unknown_keys_rejected():
    payload = {
        "data": {
            "schedules": [
                {
                    "schedule_id": SECRET,
                    "doctor_id": SECRET,
                    "doc_id": SECRET,
                    "date": "2026-10-01",
                    "time_range": "09:00-10:00 " + SECRET,
                    "hospital": SECRET,
                    "status": "available",
                    "weekday": 4,
                    "day_period": "am",
                }
            ]
        }
    }
    doc = convert_schedule(payload, **META)
    assert SECRET not in json.dumps(doc)
    row = doc["payload"]["data"]["schedules"][0]
    assert row["doctor_id"] == row["doc_id"] and row["status"] == "available"
    assert doc["metadata"]["schema_fingerprint"] == fingerprint(payload)
    payload["data"]["schedules"][0]["new_token"] = SECRET
    with pytest.raises(FixtureRejected):
        convert_schedule(payload, **META)
    with pytest.raises(FixtureRejected):
        convert_schedule({"sch": {}}, **META)


def test_fingerprint_detects_structure_not_private_values():
    a = convert_booking_html('<input id="hismemid" value="SYN_A">', **META)
    b = convert_booking_html('<input id="hismemid" value="SYN_B">', **META)
    c = convert_booking_html(
        '<input id="hismemid" value="SYN_B"><input id="sch_date" value="">', **META
    )
    assert a["metadata"]["schema_fingerprint"] == b["metadata"]["schema_fingerprint"]
    assert a["metadata"]["schema_fingerprint"] != c["metadata"]["schema_fingerprint"]


@pytest.mark.parametrize("link", ["input", "output"])
def test_cli_rejects_links_without_printing_raw_data(tmp_path, link):
    import sys

    source, output = tmp_path / "source.html", tmp_path / "fixture.json"
    source.write_text('<input name="schedule_id" value="' + SECRET + '">')
    if link == "input":
        linked = tmp_path / "linked.html"
        linked.symlink_to(source)
        source = linked
    else:
        output.symlink_to(tmp_path / "outside.json")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "grab.canary.fixtures",
            "booking",
            str(source),
            str(output),
            "--captured-on",
            "2026-09-30",
            "--level",
            "prepare",
            "--path",
            "python",
            "--source",
            "synthetic",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert SECRET not in result.stdout + result.stderr
    assert not (tmp_path / "outside.json").exists()


async def test_exported_sample_parity_in_both_browser_adapters(chromium_page):
    from pathlib import Path

    from grab.booking.page import read_snapshot
    from tests.contracts.booking.scenarios import USERSCRIPT

    sample = json.loads(
        (Path(__file__).parent / "fixtures/booking.minimal.v1.json").read_text()
    )
    page = chromium_page
    await page.context.route(
        "**/*",
        lambda route: route.fulfill(body=sample["html"], content_type="text/html"),
    )
    await page.goto("https://synthetic.invalid/booking")
    await page.evaluate("window.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__=true")
    await page.evaluate(USERSCRIPT.read_text())
    python_snapshot = await read_snapshot(page)
    js_snapshot = await page.evaluate(
        "__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.readBookingSnapshot()"
    )
    assert python_snapshot == js_snapshot == sample["snapshot"]
    selection = dict(
        member_id="synthetic-member-1",
        schedule_id="synthetic-schedule-1",
        appointment_value="synthetic-time-2",
        date="2030-01-01",
    )
    python_decision = decide(python_snapshot, selection)
    js_decision = await page.evaluate(
        "(selection)=>__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.decideBookingPreparation(__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.readBookingSnapshot(),selection)",
        selection,
    )
    assert python_decision == js_decision
    assert sample["metadata"]["source"] == "synthetic"


@pytest.mark.parametrize(
    "date_source",
    [
        '<div><span id="jzdate">2026年99月99日</span></div>',
        '<input name="sch_data" value=\'a:1:{s:4:"slot";a:1:{s:7:"to_date";s:3:"bad";}}\'>',
    ],
)
def test_invalid_source_dates_reject_export_instead_of_losing_blockers(date_source):
    raw = (
        '<input name="schedule_id" value="slot"><input type="hidden" name="mid" value="member">'
        + date_source
        + '<button id="submitbtn">预约</button>'
    )
    assert (
        "date.conflict"
        in decide(snapshot_html(raw), dict(member_id="member", schedule_id="slot"))[
            "blockers"
        ]
    )
    with pytest.raises(FixtureRejected, match="unsupported schema"):
        convert_booking_html(raw, **META)


def test_empty_schedule_time_range_preserves_deferred_hour_filter():
    from grab.services.schedule import ScheduleService

    payload = {
        "data": {
            "schedules": [
                {
                    "schedule_id": "slot",
                    "doctor_id": "doc",
                    "time_range": "",
                    "status": "available",
                }
            ]
        }
    }
    exported = convert_schedule(payload, **META)["payload"]
    assert exported["data"]["schedules"][0]["time_range"] == ""
    service = ScheduleService(None)
    before = service.filter_slots(
        service.parse_doctor_schedule(payload), [], [], [], ["09:00-09:30"]
    )
    after = service.filter_slots(
        service.parse_doctor_schedule(exported), [], [], [], ["09:00-09:30"]
    )
    assert len(before) == len(after) == 1

    from tests.userscripts.test_doctor_page_poller import _run_hook

    for value in (payload, exported):
        doctor_id = value["data"]["schedules"][0]["doctor_id"]
        result = _run_hook(
            "hooks.filterSlots(hooks.parseDoctorSchedulePayload("
            + json.dumps(value)
            + ", null), "
            + json.dumps({"doctorId": doctor_id})
            + ', {weeks:[],days:[],hours:["09:00-09:30"]}).length'
        )
        assert result == 1
