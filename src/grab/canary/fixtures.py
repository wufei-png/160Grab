"""Rebuild minimal fixtures; raw documents are never copied to the output.

Unknown form controls / JSON keys fail closed. Script, embedded JSON, event
handlers, URL attributes and arbitrary text are discarded, not regex-redacted.
The format deliberately covers only booking DOM and the normalized schedule
schema. Other live schemas require a reviewed converter before export.
"""

import argparse
import hashlib
import json
import re
from datetime import date, timedelta
from html import escape

from grab.booking.form import Tree, field_key, snapshot_html, valid_date
from grab.utils.private_files import absolute_path, private_directory


class FixtureRejected(ValueError):
    def __init__(self):
        super().__init__("Fixture rejected: unsupported schema")


class Tokens:
    def __init__(self):
        self.values = {}

    def replace(self, kind, value):
        if not value or value == "0":
            return value
        key = (kind, str(value))
        if key not in self.values:
            index = sum(k[0] == kind for k in self.values) + 1
            self.values[key] = (
                (date(2030, 1, 1) + timedelta(days=index - 1)).isoformat()
                if kind == "date" and valid_date(value)
                else f"synthetic-{kind}-{index}"
            )
        return self.values[key]


def shape(value):
    if isinstance(value, dict):
        return {k: shape(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        # Capture heterogeneity; ignore counts/values in the schema fingerprint.
        items = {json.dumps(shape(v), sort_keys=True) for v in value}
        return {"array": sorted(items)}
    return type(value).__name__


def fingerprint(value):
    return hashlib.sha256(json.dumps(shape(value), sort_keys=True).encode()).hexdigest()


def metadata(payload, *, captured_on, level, path, source, coverage):
    if (
        not valid_date(captured_on)
        or level not in {"readonly", "prepare", "submit"}
        or path not in {"python", "userscript"}
        or source not in {"synthetic", "live"}
    ):
        raise FixtureRejected()
    return {
        "schema_version": 1,
        "captured_on": captured_on,
        "level": level,
        "path": path,
        "source": source,
        "coverage": coverage,
        "schema_fingerprint": fingerprint(payload),
    }


def _time_label(value):
    if value == "":
        return ""  # No coarse time: both adapters defer precise hour filtering.
    # Keep only a strict time range used by both parsers, never free-form labels.
    match = re.search(r"(?<!\d)([0-2]\d:[0-5]\d\s*-\s*[0-2]\d:[0-5]\d)(?!\d)", value)
    return match[1].replace(" ", "") if match else "synthetic-time"


def convert_booking_html(raw, **meta):
    if len(raw) > 2_000_000:
        raise FixtureRejected()
    tree = Tree(raw)
    for n in tree.nodes:
        parent = n
        while parent and parent.attrs.get("id") != "grab160-doctor-page-poller-panel":
            parent = parent.parent
        if parent is not None:
            continue  # Tool settings are local inputs, never fixture fields.
        if n.tag in {"input", "select", "textarea"} and not field_key(n):
            name = n.attrs.get("name", "")
            if (
                name
                not in {
                    "schedule_id",
                    "sch_data",
                    "member_id",
                    "memberId",
                    "mid",
                    "his_mem_id",
                    "csrf",
                    "token",
                    "ticket",
                    "randstr",
                }
                and n.attrs.get("type") != "submit"
            ):
                raise FixtureRejected()
    snapshot = snapshot_html(raw)
    # A static jzdate DOM cannot represent every unsupported source date.
    # Reject rather than exporting an HTML version that loses date.conflict.
    if any(value and not valid_date(value) for value in snapshot["dates"]):
        raise FixtureRejected()
    tokens = Tokens()
    snapshot["schedule_ids"] = [
        tokens.replace("schedule", v) for v in snapshot["schedule_ids"]
    ]
    snapshot["hidden_members"] = [
        tokens.replace("member", v) for v in snapshot["hidden_members"]
    ]
    snapshot["dates"] = [tokens.replace("date", v) for v in snapshot["dates"]]
    for member in snapshot["members"]:
        member["ids"] = [tokens.replace("member", v) for v in member["ids"]]
        member["label"] = tokens.replace("label", member["label"])
        member["address"] = {
            k: tokens.replace("address." + k, v) for k, v in member["address"].items()
        }
    for time in snapshot["times"]:
        time["value"] = tokens.replace("time", time["value"])
        time["label"] = _time_label(time["label"])
    for key, controls in snapshot["fields"].items():
        for control in controls:
            control["value"] = (
                "1" if key == "accept" else tokens.replace(key, control["value"])
            )
            for option in control["options"]:
                option["value"] = tokens.replace(key, option["value"])
                option["label"] = tokens.replace("label", option["label"])
    html = render_booking(snapshot)
    envelope = {
        "metadata": metadata(
            snapshot,
            coverage={
                "members": len(snapshot["members"]),
                "times": len(snapshot["times"]),
                "fields": sorted(snapshot["fields"]),
                "submit_controls": len(snapshot["submit"]),
                "scripts_removed": sum(n.tag == "script" for n in tree.nodes),
                "raw_behavior_preserved": False,
            },
            **meta,
        ),
        "snapshot": snapshot,
        "html": html,
    }
    return envelope


def render_booking(s):
    def attr(value):
        return escape(str(value), quote=True)

    def flag(name, yes):
        return f" {name}" if yes else ""

    parts = ['<form id="suborder">']
    parts += [
        f'<input name="schedule_id" value="{attr(v)}">' for v in s["schedule_ids"]
    ]
    parts += [
        f'<input type="hidden" name="mid" value="{attr(v)}">'
        for v in s["hidden_members"]
    ]
    # Maintain original radio indexes, including unrelated radios before members.
    index = 0
    for m in s["members"]:
        while index < m["index"]:
            parts.append('<input type="radio" name="synthetic-other">')
            index += 1
        ids = m["ids"]
        parts.append(
            f'<label>{attr(m["label"])}<input type="radio" name="mid" value="{attr(ids[0] if ids else "")}"'
            + (f' data-member-id="{attr(ids[-1])}"' if len(ids) > 1 else "")
            + flag("checked", m["checked"])
            + flag("disabled", not m["actionable"])
            + (' need_check="1"' if m["blocked"] else "")
            + "".join(
                f' {a}="{attr(m["address"][k])}"'
                for k, a in [
                    ("province", "province_id"),
                    ("city", "city_id"),
                    ("area", "area_id"),
                    ("detail", "address"),
                ]
            )
            + "></label>"
        )
        index += 1
    parts.append('<ul id="delts">')
    for t in s["times"]:
        parts.append(
            f'<li val="{attr(t["value"])}"'
            + (' class="selected"' if t["selected"] else "")
            + flag("hidden", not t["actionable"])
            + f">{attr(t['label'])}</li>"
        )
    parts.append("</ul>")
    for d in s["dates"]:
        if valid_date(d):
            y, m, day = d.split("-")
            parts.append(f'<div><span id="jzdate">{y}年{m}月{day}日</span></div>')
    identifiers = {
        "card": "hismemid",
        "date": "sch_date",
        "accept": "check_yuyue_rule",
        "disease_input": "disease_input",
        "disease_content": "disease_content",
        **{
            "address." + k: "useraddress_" + k
            for k in ("province", "city", "area", "detail")
        },
    }
    for key, controls in s["fields"].items():
        for c in controls:
            tag = c["kind"]
            start = f'<{tag} id="{identifiers[key]}" value="{attr(c["value"])}"' + flag(
                "readonly", not c["actionable"]
            )
            if key == "accept":
                start += ' type="checkbox"' + flag("checked", c["checked"])
            if tag == "input":
                parts.append(start + ">")
            elif tag == "textarea":
                parts.append(start + f">{attr(c['value'])}</textarea>")
            else:
                parts.append(start + flag("disabled", not c["actionable"]) + ">")
                parts += [
                    f'<option value="{attr(o["value"])}"'
                    + flag("selected", o["value"] == c["value"])
                    + flag("disabled", o["disabled"])
                    + f">{attr(o['label'])}</option>"
                    for o in c["options"]
                ]
                parts.append("</select>")
    for ready in s["other_required"]:
        parts.append(
            '<input required name="synthetic-required" value="'
            + ("synthetic-value" if ready else "")
            + '">'
        )
    parts += [
        '<button type="button" id="submitbtn"'
        + flag("disabled", not actionable)
        + ">预约</button>"
        for actionable in s["submit"]
    ]
    parts.append("</form>")
    return "".join(parts)


SLOT_KEYS = {
    "schedule_id",
    "doctor_id",
    "weekday",
    "day_period",
    "hospital",
    "department",
    "doctor",
    "date",
    "time_range",
    "status",
    "unit_id",
    "dep_id",
    "doc_id",
}


def convert_schedule(payload, **meta):
    # Only the normalized, explicitly known schema. Unknown provider schemas fail
    # rather than being mislabeled as an empty successful response.
    if (
        not isinstance(payload, dict)
        or set(payload) - {"data", "result_code"}
        or "data" not in payload
        or not isinstance(payload["data"], dict)
        or set(payload["data"]) != {"schedules"}
        or not isinstance(payload["data"]["schedules"], list)
    ):
        raise FixtureRejected()
    if "result_code" in payload and (
        type(payload["result_code"]) is not int or payload["result_code"] not in {0, 1}
    ):
        raise FixtureRejected()
    tokens, rows = Tokens(), []
    for row in payload["data"]["schedules"]:
        if not isinstance(row, dict) or set(row) - SLOT_KEYS:
            raise FixtureRejected()
        clean = {}
        for key, value in row.items():
            if key == "weekday":
                if type(value) is not int or not 0 <= value <= 7:
                    raise FixtureRejected()
                clean[key] = value
            elif not isinstance(value, str):
                raise FixtureRejected()
            elif key == "status":
                if value not in {
                    "available",
                    "full",
                    "expired",
                    "stopped",
                    "not_open",
                    "unavailable",
                }:
                    raise FixtureRejected()
                clean[key] = value
            elif key == "day_period":
                if value not in {"am", "pm", "em", ""}:
                    raise FixtureRejected()
                clean[key] = value
            elif key == "time_range":
                clean[key] = _time_label(value)
            else:
                clean[key] = tokens.replace(
                    "doctor_id" if key == "doc_id" else key, value
                )
        rows.append(clean)
    clean = {"data": {"schedules": rows}}
    if "result_code" in payload:
        clean["result_code"] = payload["result_code"]
    return {
        "metadata": metadata(payload, coverage={"slots": len(rows)}, **meta),
        "payload": clean,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Export a minimal sanitized local fixture"
    )
    parser.add_argument("kind", choices=["booking", "schedule"])
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--captured-on", required=True)
    parser.add_argument(
        "--level", required=True, choices=["readonly", "prepare", "submit"]
    )
    parser.add_argument("--path", required=True, choices=["python", "userscript"])
    parser.add_argument("--source", required=True, choices=["synthetic", "live"])
    args = parser.parse_args()
    try:
        source, output = absolute_path(args.input), absolute_path(args.output)
        with private_directory(source.parent, create=False) as directory:
            raw = directory.read(source.name)
        if len(raw) > 2_000_000:
            raise FixtureRejected()
        meta = dict(
            captured_on=args.captured_on,
            level=args.level,
            path=args.path,
            source=args.source,
        )
        document = (
            convert_booking_html(raw.decode(), **meta)
            if args.kind == "booking"
            else convert_schedule(json.loads(raw), **meta)
        )
        with private_directory(output.parent) as directory:
            directory.create(
                output.name, json.dumps(document, ensure_ascii=False, indent=2).encode()
            )
    except Exception:
        parser.exit(1, "Fixture export rejected; no raw data printed.\n")


if __name__ == "__main__":
    main()
