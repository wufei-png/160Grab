"""Submission boundary counterexamples, using routed synthetic pages only."""

import asyncio

import pytest
from playwright.async_api import Error as PlaywrightError

from grab.models.schemas import BookingForm, BookingState, DoctorPageTarget, GrabConfig
from grab.services.booking import PageBookingStrategy
from grab.transactions.evidence import BookingEvidence
from grab.transactions.store import AttemptStore
from tests.contracts.booking.scenarios import USERSCRIPT

BOOKING = "https://www.91160.com/guahao/ystep1/uid-u/depid-d/schid-slot.html"
HTML = """<form id="suborder"><input name="schedule_id" value="slot">
<input type="hidden" name="member_id" value="member">
<button id="submitbtn" type="submit">Synthetic submit</button></form>"""
HOOKS = "__GRAB160_DOCTOR_POLLER_TEST_HOOKS__"


async def load(page, html=HTML, *, change_target=False):
    async def serve(route):
        if change_target and route.request.url == BOOKING:
            # Change the URL during loading without making a redirected request.
            target = BOOKING.replace("uid-u/depid-d", "uid-other/depid-other")
            await route.fulfill(
                body=f'<script>history.replaceState(null, "", "{target}")</script>'
                + html,
                content_type="text/html",
            )
        else:
            await route.fulfill(body=html, content_type="text/html")

    await page.context.route("**/*", serve)
    await page.add_init_script("""(() => {
        window.clicks = 0; window.submits = 0;
        document.addEventListener('click', e => {
            if (e.target.closest('#submitbtn')) clicks++;
        });
        document.addEventListener('submit', e => {e.preventDefault(); submits++;});
    })()""")
    await page.goto(BOOKING)


def strategy(page, root):
    obj = PageBookingStrategy(
        page,
        GrabConfig(page_action_sleep_time="0"),
        attempt_store=AttemptStore(root),
        authorization=lambda *_: True,
    )
    obj.prepare(
        DoctorPageTarget(unit_id="u", dept_id="d", doctor_id="doc", source_url=""),
        "member",
    )
    return obj


async def run_js(page, *, stale=None):
    await page.evaluate("window.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__ = true")
    await page.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
    page.on("dialog", lambda dialog: dialog.accept())
    return await page.evaluate(
        """async stale => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        h.writeSettings({settingsVersion:5, member:{memberId:'member'},
            target:stale ? {} : {unitId:'u',depId:'d',doctorId:'doc'},
            booking:{submitMode:'auto'},pacing:{pageActionMs:[0,0]}});
        if (stale) h.writeState({lastTarget:stale});
        const id = h.prepareManualControllerStart('booking');
        await h.runBookingPageController(id);
        return {clicks,submits,pending:h.submissionBlocked(),state:h.readState().outcome};
    }""",
        stale,
    )


async def test_python_changed_target_stops_before_preparation(chromium_page, tmp_path):
    page = chromium_page
    await load(page, change_target=True)
    obj = strategy(page, tmp_path)
    result = await obj.submit_with_retry("slot")
    assert result.state == "AWAITING_MANUAL_CONFIRMATION"
    assert await page.evaluate("clicks") == 0
    assert not obj.attempt_store.pending()


async def test_offline_browser_blocks_redirect_that_bypasses_routes(chromium_page):
    connections = []
    requests = []
    chromium_page.on(
        "request",
        lambda request: requests.append(
            (request.url, request.redirected_from is not None)
        ),
    )

    async def accept(_reader, writer):
        connections.append(True)
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    redirect_url = f"http://127.0.0.1:{port}/"
    try:
        await chromium_page.context.route(
            "https://synthetic.invalid/start",
            lambda route: route.fulfill(status=302, headers={"location": redirect_url}),
        )
        with pytest.raises(PlaywrightError):
            await chromium_page.goto("https://synthetic.invalid/start", timeout=3000)
        await asyncio.sleep(0.05)
        assert (redirect_url, True) in requests
        assert not connections
    finally:
        server.close()
        await server.wait_closed()


async def test_python_rechecks_after_actionability_wait(chromium_page, tmp_path):
    page = chromium_page
    await load(
        page,
        HTML
        + '<div id="cover" style="position:fixed;inset:0;z-index:9999;background:gray"></div>',
    )
    await page.evaluate("""() => setTimeout(() => {
        document.querySelector('[name="member_id"]').value = 'other';
        document.querySelector('#cover').remove();
    }, 400)""")
    obj = strategy(page, tmp_path)
    result = await obj.submit_open_form(
        BookingForm(member_id="member", schedule_id="slot")
    )
    assert result.state == "AWAITING_MANUAL_CONFIRMATION"
    assert await page.evaluate("clicks") == 0
    assert not obj.attempt_store.pending()


@pytest.mark.parametrize(
    "evidence_state",
    [None, BookingState.CONFIRMED_SUCCESS, BookingState.CONFIRMED_NO_EFFECT],
)
async def test_python_capture_guard_fences_changes_after_durable_begin(
    chromium_page, tmp_path, monkeypatch, evidence_state
):
    page = chromium_page
    await load(page)
    original = page.locator

    class DelayedControl:
        def __init__(self, locator):
            self.locator = locator

        def __getattr__(self, name):
            return getattr(self.locator, name)

        async def click(self, **kwargs):
            if not kwargs.get("trial"):
                await page.evaluate(
                    "document.querySelector('[name=member_id]').value = 'other'"
                )
            await self.locator.click(**kwargs)

    monkeypatch.setattr(
        page, "locator", lambda selector: DelayedControl(original(selector))
    )
    obj = strategy(page, tmp_path)
    evidence_calls = []

    async def adapter(*_args):
        evidence_calls.append(True)
        return BookingEvidence(evidence_state, "u", "d", "doc", "member", "slot", None)

    if evidence_state is not None:
        obj.evidence_adapter = adapter
    result = await obj.submit_open_form(
        BookingForm(member_id="member", schedule_id="slot")
    )
    assert result.state == "OUTCOME_UNKNOWN"
    assert await page.evaluate("clicks") == 0
    assert await page.evaluate("submits") == 0
    assert obj.attempt_store.pending()  # Never erase a transaction already begun.
    assert not evidence_calls


async def test_python_capture_guard_allows_evidence_after_unchanged_click(
    chromium_page, tmp_path
):
    page = chromium_page
    await load(page)
    obj = strategy(page, tmp_path)

    async def adapter(*_args):
        return BookingEvidence(
            BookingState.CONFIRMED_SUCCESS, "u", "d", "doc", "member", "slot", None
        )

    obj.evidence_adapter = adapter
    result = await obj.submit_open_form(
        BookingForm(member_id="member", schedule_id="slot")
    )
    assert result.state == BookingState.CONFIRMED_SUCCESS
    assert await page.evaluate("clicks") == 1
    assert not obj.attempt_store.pending()


async def test_python_capture_guard_state_loss_keeps_attempt_unknown(
    chromium_page, tmp_path, monkeypatch
):
    page = chromium_page
    await load(page)
    original = page.locator

    class LostGuardControl:
        def __init__(self, locator):
            self.locator = locator

        def __getattr__(self, name):
            return getattr(self.locator, name)

        async def click(self, **kwargs):
            await self.locator.click(**kwargs)
            if not kwargs.get("trial"):
                await page.evaluate("""() => Object.keys(globalThis)
                    .filter(key => key.startsWith('__grab160_submit_guard_'))
                    .forEach(key => delete globalThis[key])""")

    monkeypatch.setattr(
        page, "locator", lambda selector: LostGuardControl(original(selector))
    )
    obj = strategy(page, tmp_path)
    evidence_calls = []

    async def adapter(*_args):
        evidence_calls.append(True)
        return BookingEvidence(
            BookingState.CONFIRMED_SUCCESS, "u", "d", "doc", "member", "slot", None
        )

    obj.evidence_adapter = adapter
    result = await obj.submit_open_form(
        BookingForm(member_id="member", schedule_id="slot")
    )
    assert result.state == BookingState.OUTCOME_UNKNOWN
    assert await page.evaluate("clicks") == 1
    assert not evidence_calls
    assert obj.attempt_store.pending()


@pytest.mark.parametrize("adapter", ["python", "userscript"])
@pytest.mark.parametrize("invalid", ["email", "custom"])
async def test_invalid_optional_booking_control_blocks_before_journal(
    chromium_page, tmp_path, adapter, invalid
):
    page = chromium_page
    field = (
        '<input name="extra" type="email" value="invalid">'
        if invalid == "email"
        else '<input name="extra" value="existing">'
    )
    await load(page, HTML.replace("<button", field + "<button"))
    if invalid == "custom":
        await page.locator('[name="extra"]').evaluate(
            "node => node.setCustomValidity('SYNTHETIC_PRIVATE_MESSAGE')"
        )
    if adapter == "python":
        obj = strategy(page, tmp_path)
        result = await obj.submit_open_form(
            BookingForm(member_id="member", schedule_id="slot")
        )
        assert result.state == "AWAITING_MANUAL_CONFIRMATION"
        assert not obj.attempt_store.pending()
    else:
        result = await run_js(page)
        assert not result["pending"]
    assert await page.evaluate("clicks") == 0
    assert await page.evaluate("submits") == 0


@pytest.mark.parametrize("same_department", [False, True])
async def test_userscript_does_not_reuse_unbound_last_doctor(
    chromium_page, same_department
):
    page = chromium_page
    await load(page)
    result = await run_js(
        page,
        stale={
            "unitId": "u" if same_department else "old",
            "depId": "d" if same_department else "old",
            "doctorId": "old-doctor",
        },
    )
    assert result["clicks"] == 0
    assert not result["pending"]


@pytest.mark.parametrize("adapter", ["python", "userscript"])
async def test_unrelated_optional_invalid_control_does_not_block_booking(
    chromium_page, tmp_path, adapter
):
    page = chromium_page
    await load(page, HTML + '<form><input type="email" value="invalid"></form>')
    if adapter == "python":
        obj = strategy(page, tmp_path)
        await obj.submit_open_form(BookingForm(member_id="member", schedule_id="slot"))
    else:
        await run_js(page)
    assert await page.evaluate("clicks") == 1
