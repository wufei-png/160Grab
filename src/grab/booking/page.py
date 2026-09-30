"""Playwright adapter. Snapshot values remain in memory, never in diagnostics."""

from grab.booking.form import (
    FIELD_SELECTORS,
    MEMBER_SELECTOR,
    TIME_SELECTOR,
    decide,
    snapshot_html,
)

# Clone to capture live properties (outerHTML alone retains stale default values).
SNAPSHOT_SCRIPT = """() => {
    const clone = document.documentElement.cloneNode(true);
    const live = document.documentElement.querySelectorAll('*');
    const copies = clone.querySelectorAll('*');
    live.forEach((node, i) => {
        const copy = copies[i];
        if ('value' in node) copy.setAttribute('value', node.value);
        if (node.tagName === 'TEXTAREA') copy.textContent = node.value;
        if ('checked' in node) copy.toggleAttribute('checked', node.checked);
        if (node.validity) copy.setAttribute('data-grab-valid', String(node.validity.valid));
        if (node.tagName === 'OPTION') copy.toggleAttribute('selected', node.selected);
        const style = getComputedStyle(node);
        const visible = Boolean(node.getClientRects().length) && style.visibility !== 'hidden';
        copy.setAttribute('data-grab-writable', String(!node.matches(':disabled') && !node.readOnly && (visible || node.type === 'hidden')));
        copy.setAttribute('data-grab-actionable', String(visible && !node.matches(':disabled') && node.getAttribute('aria-disabled') !== 'true'));
    });
    clone.querySelectorAll('script').forEach(n => n.remove());
    return clone.outerHTML;
}"""


async def read_snapshot(page):
    if hasattr(page, "evaluate"):
        html = await page.evaluate(SNAPSHOT_SCRIPT)
    else:
        html = await page.content()
    return snapshot_html(html)


def selection_for(form):
    return dict(
        member_id=form.member_id,
        schedule_id=form.schedule_id,
        appointment_value=form.appointment_value,
        date=form.schedule_date,
    )


def configured_values(config):
    if not config:
        return {}
    b = config.booking
    return {
        "card": b.clinic_card,
        "disease_input": b.disease_description,
        "disease_content": b.disease_description,
        **{"address." + k: v for k, v in b.address.model_dump().items()},
    }


async def read_decision(page, form, config):
    snapshot = await read_snapshot(page)
    decision = decide(snapshot, selection_for(form), configured_values(config))
    return snapshot, decision


def exact_attribute(name, value):
    escaped = "".join("\\" + format(ord(c), "x") + " " for c in value)
    return f'[ {name}="{escaped}" ]'


async def apply_decision(page, snapshot, decision, guard=lambda: None):
    if not decision["can_prepare"]:
        return
    # Index is from the full radio set, validated by exact identity above.
    if decision["member_index"] is not None:
        member = page.locator(
            MEMBER_SELECTOR + exact_attribute("value", decision["member_id"])
        )
        if await member.count() != 1:
            return
        if not await member.is_checked():
            guard()
            await member.check(timeout=1000)
    if decision["appointment_value"]:
        matches = [
            i
            for i, t in enumerate(snapshot["times"])
            if t["value"] == decision["appointment_value"]
        ]
        if len(matches) == 1 and not snapshot["times"][matches[0]]["selected"]:
            time = page.locator(
                TIME_SELECTOR + exact_attribute("val", decision["appointment_value"])
            )
            if await time.count() != 1:
                return
            guard()
            await time.click(timeout=1000)
    for write in decision["writes"]:
        # Re-read after each event: cascading selects can change asynchronously,
        # and member selection can populate fields that must never be overwritten.
        control = page.locator(FIELD_SELECTORS[write["field"]])
        if await control.count() != 1:
            return
        existing = (await control.input_value()).strip()
        if existing and not (write["kind"] == "select" and existing == "0"):
            continue
        if write["kind"] == "select":
            guard()
            await control.select_option(value=write["value"], timeout=1000)
        elif await control.get_attribute("type") == "hidden":
            guard()
            await control.evaluate(
                "(node, value) => { node.value = value; node.dispatchEvent(new Event('input', {bubbles:true})); node.dispatchEvent(new Event('change', {bubbles:true})); }",
                write["value"],
            )
        else:
            guard()
            await control.fill(write["value"], timeout=1000)


def selected_member_ready(snapshot, decision):
    index = decision["member_index"]
    checked = [m for m in snapshot["members"] if m["checked"]]
    member_ready = (
        index is None or len(checked) == 1 and checked[0]["index"] == index
    ) and all(value == decision["member_id"] for value in snapshot["hidden_members"])

    time_value = decision["appointment_value"]
    return member_ready and (
        not time_value
        or [t["value"] for t in snapshot["times"] if t["selected"]] == [time_value]
    )
