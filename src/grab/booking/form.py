"""Private, pure booking snapshots/decisions. Never send their values to logs."""

import re
from datetime import date
from html.parser import HTMLParser

from grab.booking.serialized import schedule_record_dates

FIELD_SELECTORS = {
    "card": '#hismemid, [name="hisMemId"], [name="hismemid"]',
    "date": '#sch_date, [name="sch_date"]',
    "disease_input": '#disease_input, [name="disease_input"]',
    "disease_content": '#disease_content, [name="disease_content"]',
    "address.province": "#useraddress_province",
    "address.city": "#useraddress_city",
    "address.area": '#useraddress_area, select[name="addressId"]',
    "address.detail": '#useraddress_detail, input[name="address"]',
    "accept": '#check_yuyue_rule, input[name="accept"][value="1"]',
}
MEMBER_SELECTOR = 'input[type="radio"]'
TIME_SELECTOR = "#delts li[val]"
SUBMIT_SELECTOR = (
    "#submitbtn, #submit_booking, #submitBooking, "
    '#suborder button[type="submit"], #suborder input[type="submit"]'
)


class Node:
    def __init__(self, tag, attrs, parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.children = []
        self.text = ""

    def options(self):
        return [
            option
            for child in self.children
            for option in ([child] if child.tag == "option" else child.options())
        ]

    def value(self):
        if self.tag == "textarea":
            return self.text.strip()
        if self.tag == "select":
            options = self.options()
            selected = next((n for n in options if "selected" in n.attrs), None)
            return (selected or (options[0] if options else self)).attrs.get(
                "value", ""
            )
        return self.attrs.get("value", "").strip()

    def actionable(self):
        node = self
        while node:
            a = node.attrs
            if (
                "hidden" in a
                or "disabled" in a
                or a.get("aria-disabled") == "true"
                or re.search(
                    r"(display\s*:\s*none|visibility\s*:\s*hidden)", a.get("style", "")
                )
                or a.get("data-grab-actionable") == "false"
            ):
                return False
            node = node.parent
        return True


class Tree(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node("root", [])
        self.stack, self.nodes = [self.root], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        if tag not in {
            "input",
            "br",
            "img",
            "meta",
            "link",
            "hr",
            "wbr",
            "area",
            "base",
            "embed",
            "source",
            "param",
        }:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self.stack = self.stack[:i]
                break

    def handle_data(self, data):
        for node in self.stack:
            node.text += data


def field_key(n):
    a, name, ident = n.attrs, n.attrs.get("name"), n.attrs.get("id")
    if n.tag not in {"input", "select", "textarea"}:
        return None
    if ident == "hismemid" or name in {"hisMemId", "hismemid"}:
        return "card"
    if ident == "sch_date" or name == "sch_date":
        return "date"
    for key in ("disease_input", "disease_content"):
        if ident == key or name == key:
            return key
    for key in ("province", "city", "area", "detail"):
        if ident == "useraddress_" + key:
            return "address." + key
    if name == "addressId" and n.tag == "select":
        return "address.area"
    if name == "address" and n.tag == "input":
        return "address.detail"
    if ident == "check_yuyue_rule" or (name == "accept" and a.get("value") == "1"):
        return "accept"
    return None


def form_owner(n):
    if n.attrs.get("form"):
        return ("id", n.attrs["form"])
    parent = n.parent
    while parent and parent.tag != "form":
        parent = parent.parent
    return ("id", parent.attrs["id"]) if parent and parent.attrs.get("id") else parent


def required_ready(n, nodes):
    # Browser snapshots include native validity without firing invalid events.
    if "data-grab-valid" in n.attrs:
        return n.attrs["data-grab-valid"] == "true"
    if "disabled" in n.attrs:
        return True
    if n.attrs.get("type") == "radio":
        name = n.attrs.get("name")
        return any(
            "checked" in candidate.attrs
            for candidate in nodes
            if candidate is n
            or name
            and candidate.tag == "input"
            and candidate.attrs.get("type") == "radio"
            and candidate.attrs.get("name") == name
            and form_owner(candidate) == form_owner(n)
        )
    if n.attrs.get("type") == "checkbox":
        return "checked" in n.attrs
    return bool(n.value())


def snapshot_html(html):
    tree = Tree(html)
    result = {
        "version": 1,
        "schedule_ids": [],
        "members": [],
        "hidden_members": [],
        "times": [],
        "dates": [],
        "fields": {},
        "submit": [],
        "other_required": [],
    }
    radio_index = 0
    for n in tree.nodes:
        a, name = n.attrs, n.attrs.get("name", "")
        if name == "schedule_id":
            result["schedule_ids"].append(n.value())
        if (
            name in {"member_id", "memberId", "mid", "his_mem_id"}
            and a.get("type") == "hidden"
        ):
            result["hidden_members"].append(n.value())
        if n.tag == "input" and a.get("type") == "radio":
            parent = n.parent
            container = None
            while parent:
                if parent.tag in {"tr", "li", "label"} or any(
                    c in parent.attrs.get("class", "").split()
                    for c in {"patient_item", "member_item", "person_item"}
                ):
                    container = parent
                    break
                parent = parent.parent
            if (
                name.lower() in {"mid", "member_id", "memberid", "his_mem_id"}
                or "data-member-id" in a
                or "data-mid" in a
            ):
                ids = list(
                    dict.fromkeys(
                        v
                        for v in [n.value(), a.get("data-member-id"), a.get("data-mid")]
                        if v
                    )
                )
                result["members"].append(
                    {
                        "ids": ids,
                        "label": " ".join((container or n.parent).text.split()),
                        "index": radio_index,
                        "actionable": n.actionable(),
                        "checked": "checked" in a,
                        "blocked": a.get("need_check") == "1"
                        or any(
                            a.get(k) == "0"
                            for k in [
                                "record_created",
                                "is_complete",
                                "is_info_complete",
                            ]
                        )
                        or bool(
                            re.search(
                                r"暂不能预约|审核中|认证|建档",
                                a.get("data-title", "") + a.get("title", ""),
                            )
                        ),
                        "address": {
                            k: a.get(v, "")
                            for k, v in [
                                ("province", "province_id"),
                                ("city", "city_id"),
                                ("area", "area_id"),
                                ("detail", "address"),
                            ]
                        },
                    }
                )
            radio_index += 1
        parent = n.parent
        in_times = False
        in_form = False
        while parent:
            in_times |= parent.attrs.get("id") == "delts"
            in_form |= parent.attrs.get("id") == "suborder"
            parent = parent.parent
        if n.tag == "li" and "val" in a and in_times:
            result["times"].append(
                {
                    "value": a["val"].strip(),
                    "label": " ".join(n.text.split()),
                    "actionable": n.actionable(),
                    "selected": "selected" in a.get("class", "").split(),
                }
            )
        if a.get("id") in {"submitbtn", "submit_booking", "submitBooking"} or (
            in_form and n.tag in {"input", "button"} and a.get("type") == "submit"
        ):
            result["submit"].append(n.actionable())
        key = field_key(n)
        if key:
            result["fields"].setdefault(key, []).append(
                {
                    "value": n.value(),
                    "checked": "checked" in a,
                    "actionable": (
                        a.get("data-grab-writable") == "true"
                        if "data-grab-writable" in a
                        else n.actionable() and "readonly" not in a
                    ),
                    "kind": n.tag,
                    "options": [
                        {
                            "value": c.attrs.get("value", ""),
                            "label": " ".join(c.text.split()),
                            "disabled": "disabled" in c.attrs
                            or c.parent.tag == "optgroup"
                            and "disabled" in c.parent.attrs,
                        }
                        for c in n.options()
                    ],
                }
            )
        elif "required" in a and name not in {
            "schedule_id",
            "member_id",
            "memberId",
            "mid",
            "his_mem_id",
        }:
            result["other_required"].append(required_ready(n, tree.nodes))
    # Nonempty recognized fields still need native constraint validation.
    # Missing known values have their own blockers and may be filled first.
    # Keep only the boolean: custom validation messages can contain private data.
    result["other_required"].extend(
        False
        for n in tree.nodes
        if field_key(n) and n.value() and n.attrs.get("data-grab-valid") == "false"
    )
    # Dates must belong to the unique selected schedule record, not its siblings.
    for n in tree.nodes:
        if n.attrs.get("name") == "sch_data" and len(result["schedule_ids"]) == 1:
            result["dates"].extend(
                schedule_record_dates(n.value(), result["schedule_ids"][0])
            )
        if n.attrs.get("id") == "jzdate":
            result["dates"].extend(
                "-".join(m)
                for m in re.findall(r"(20\d{2})年(\d{2})月(\d{2})日", n.parent.text)
            )
    return result


def valid_date(value):
    try:
        return (
            bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))
            and date.fromisoformat(value).isoformat() == value
        )
    except (ValueError, TypeError):
        return False


def decide(snapshot, selection, values=None):
    """One conservative decision, with private write proposals; no DOM effects."""
    values = values or {}
    blockers, writes, sources = [], [], {}
    member_id, member_index = selection.get("member_id"), None
    members = snapshot["members"]
    matches = [m for m in members if member_id in m["ids"]] if member_id else []
    if not member_id and selection.get("member_label"):
        matches = [m for m in members if m["label"] == selection["member_label"]]
    if not member_id and not selection.get("member_label"):
        matches = members if len(members) == 1 else []
    if len(matches) == 1:
        m = matches[0]
        member_id = member_id or m["ids"][0]
        member_index = m["index"]
        if len(m["ids"]) != 1 or not m["actionable"] or m["blocked"]:
            blockers.append("member.blocked")
    elif (
        not matches
        and not members
        and member_id
        and snapshot["hidden_members"] == [member_id]
    ):
        m = None
    elif (
        not matches
        and not members
        and not member_id
        and len(snapshot["hidden_members"]) == 1
        and snapshot["hidden_members"][0]
    ):
        member_id, m = snapshot["hidden_members"][0], None
    else:
        m = None
        blockers.append("member.mismatch" if member_id else "member.ambiguous")
    if len(snapshot["schedule_ids"]) != 1 or snapshot["schedule_ids"][
        0
    ] != selection.get("schedule_id"):
        blockers.append("schedule.mismatch")
    time_value = selection.get("appointment_value")
    times = [t for t in snapshot["times"] if t["value"] == time_value]
    if snapshot["times"] or time_value:
        if len(times) != 1 or not times[0]["actionable"]:
            blockers.append("time.mismatch")
    dates = list(
        dict.fromkeys(d for d in [*snapshot["dates"], selection.get("date")] if d)
    )
    if any(not valid_date(d) for d in dates) or len(dates) > 1:
        blockers.append("date.conflict")
    for key, controls in snapshot["fields"].items():
        if len(controls) != 1:
            blockers.append(key + ".ambiguous")
            continue
        f = controls[0]
        existing = f["value"]
        desired = values.get(key) or ""
        source = "config" if desired else None
        if key.startswith("address.") and m:
            member_value = m["address"].get(key.split(".")[1]) or ""
            if member_value:
                if desired and desired != member_value:
                    # Resolve region labels against exact, unique option values.
                    options = [
                        o
                        for o in f["options"]
                        if o["value"] == desired or o["label"] == desired
                    ]
                    if not (len(options) == 1 and options[0]["value"] == member_value):
                        blockers.append(key + ".conflict")
                desired, source = member_value, "member"
        if key == "date":
            desired, source = (dates[0], "schedule") if len(dates) == 1 else ("", None)
        if key == "accept":
            if not f["checked"]:
                blockers.append("accept.required")
            sources[key] = "existing" if f["checked"] else "missing"
            continue
        if f["kind"] == "select":
            if existing == "0":
                existing = ""
            if desired:
                options = [
                    o
                    for o in f["options"]
                    if not o["disabled"]
                    and o["value"] not in {"", "0"}
                    and (o["value"] == desired or o["label"] == desired)
                ]
                if len(options) != 1:
                    blockers.append(key + ".option")
                    continue
                desired = options[0]["value"]
        if existing:
            sources[key] = "existing"
            if (
                desired
                and desired != existing
                or key == "date"
                and not valid_date(existing)
            ):
                blockers.append(key + ".conflict")
        elif desired:
            sources[key] = source
            if not f["actionable"]:
                blockers.append(key + ".disabled")
            else:
                writes.append({"field": key, "value": desired, "kind": f["kind"]})
        else:
            sources[key] = "missing"
            blockers.append(key + ".required")
    if not all(snapshot["other_required"]):
        blockers.append("other.required")
    if snapshot["submit"] != [True]:
        blockers.append("submit.control")
    blockers = sorted(set(blockers))
    hard = any(not b.endswith((".required", ".option")) for b in blockers)
    return {
        "state": "AWAITING_MANUAL_CONFIRMATION" if blockers else "PREPARED",
        "blockers": blockers,
        "member_id": member_id,
        "member_index": member_index,
        "schedule_id": selection.get("schedule_id"),
        "appointment_value": time_value,
        "sources": sources,
        "writes": [] if hard else writes,
        "can_prepare": not hard,
    }
