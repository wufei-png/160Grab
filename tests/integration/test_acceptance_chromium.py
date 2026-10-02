"""S09 cross-module acceptance, exclusively synthetic routed browser content.

User login/ready and member-page transport are local seams. The real runner,
page AJAX, schedule/parser, preparation, consent, leader, journal and output
projections execute together; no live result adapter is installed.
"""

from urllib.parse import urlparse

import pytest
from loguru import logger as raw_logger

import main as cli
from grab.browser.playwright_client import PlaywrightClient
from grab.core.leader import leader_scope
from grab.models.schemas import GrabConfig
from grab.observability.notifications import (
    HttpWebhookNotifier,
    NotificationManager,
    NullDesktopNotifier,
)
from grab.observability.reporter import JsonlEventSink, RunReporter
from grab.transactions.consent import ConsentManager
from grab.transactions.store import AttemptStore
from tests.contracts.booking.scenarios import USERSCRIPT
from tests.observability.test_privacy import CANARIES, POISON, assert_clean

ORIGIN = "https://www.91160.com"
DOCTOR = ORIGIN + "/doctors/index/unit_id-SYN_UNIT/dep_id-SYN_DEPT/docid-SYN_DOC.html"
BOOKING = ORIGIN + "/guahao/ystep1/uid-SYN_UNIT/depid-SYN_DEPT/schid-SYN_SLOT.html"
HOOKS = "__GRAB160_DOCTOR_POLLER_TEST_HOOKS__"
HTML = f"""<form id="suborder">
<input name="schedule_id" value="SYN_SLOT">
<input type="radio" name="mid" value="{CANARIES[3]}">
<ul id="delts"><li val="SYN_TIME">09:00-09:30</li></ul>
<input id="sch_date" value="2030-01-02"><input id="hismemid">
<textarea name="disease_input"></textarea>
<button id="submitbtn" type="button">Synthetic submit</button></form>"""
PAYLOAD = {
    "result_code": 1,
    "data": {
        "schedules": [
            {
                "schedule_id": slot,
                "doctor_id": "SYN_DOC",
                "status": status,
                "weekday": 3,
                "day_period": "am",
                "date": "2030-01-02",
                "time_range": "09:00-09:30",
            }
            for slot, status in (
                ("SYN_FULL_SLOT", "full"),
                ("SYN_SLOT", "available"),
                ("SYN_OTHER_SLOT", "available"),
            )
        ]
    },
}


async def install_site(page, *, userscript=False, expire_first=False):
    counts = {"poll": 0, "booking": 0, "click": 0}
    unexpected = []

    async def serve(route):
        url = urlparse(route.request.url)
        if url.hostname == "gate.91160.com" and url.path == "/guahao/v1/pc/sch/doctor":
            counts["poll"] += 1
            await route.fulfill(
                json={"error_code": 10021}
                if expire_first and counts["poll"] == 1
                else PAYLOAD,
                headers={
                    "Access-Control-Allow-Origin": ORIGIN,
                    "Access-Control-Allow-Credentials": "true",
                },
            )
        elif url.hostname == "www.91160.com" and url.path == urlparse(BOOKING).path:
            counts["booking"] += 1
            await route.fulfill(body=HTML, content_type="text/html")
        elif url.hostname == "www.91160.com" and url.path == urlparse(DOCTOR).path:
            await route.fulfill(
                body='<table><tr id="mem'
                + CANARIES[3]
                + '"><td>'
                + CANARIES[0]
                + "</td><td>已认证</td></tr></table>",
                content_type="text/html",
            )
        else:
            unexpected.append((url.hostname, url.path))
            await route.abort()

    await page.context.route("**/*", serve)
    await page.expose_function(
        "syntheticClick", lambda: counts.update(click=counts["click"] + 1)
    )
    init = """
      window.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__ = true;
      window._user_key = 'SYN_TOKEN_Q';
      document.addEventListener('click', e => {
        if (e.target.matches('#delts li')) e.target.classList.add('selected');
        if (e.target.matches('#submitbtn')) {
          window.syntheticClick();
          localStorage.setItem('syntheticClicks', String(Number(localStorage.getItem('syntheticClicks') || 0) + 1));
          window.clickJournal = localStorage.getItem('grab160.submissionJournal.v1');
          document.body.append('页面已变化');
        }
      });
    """
    # Keep initialization and userscript in one script; Playwright does not
    # guarantee the order of multiple add_init_script registrations.
    await page.add_init_script(init + (USERSCRIPT.read_text() if userscript else ""))
    await page.goto(DOCTOR)
    return counts, unexpected


@pytest.mark.parametrize(
    "consent", ["accept", "recover", "reject", "noninteractive", "manual"]
)
async def test_python_runner_consent_unknown_recovery_and_privacy(
    chromium_page,
    tmp_path,
    monkeypatch,
    consent,
):
    page = chromium_page
    counts, unexpected = await install_site(page, expire_first=consent == "recover")
    config = GrabConfig(
        member_id=CANARIES[3],
        hours=["9-9.5"],
        brush_start_date="2030-01-02",
        page_action_sleep_time="0",
        booking={
            "submit_mode": "manual_confirm" if consent == "manual" else "auto",
            "clinic_card": CANARIES[5],
            "disease_description": POISON,
        },
    )
    store = AttemptStore(tmp_path / "transactions")
    prompts, logs, notifications = [], [], []

    def confirm(message):
        prompts.append(message)
        return "AUTHORIZE" if consent in {"accept", "recover"} else ""

    async def post_json(_url, payload, _timeout, _headers):
        notifications.append(payload)
        raise RuntimeError(POISON)  # delivery failure cannot change the result

    sink = JsonlEventSink(tmp_path / "logs", "a" * 12)
    reporter = RunReporter(
        sink=sink,
        rate_limit_threshold=3,
        notification_manager=NotificationManager(
            desktop_notifier=NullDesktopNotifier(),
            webhook_notifier=HttpWebhookNotifier(
                url="https://synthetic.invalid/", timeout_seconds=1, post_json=post_json
            ),
        ),
    )
    login_calls = []
    interactive = consent != "noninteractive"
    monkeypatch.setattr(
        cli, "AttemptStore", lambda: AttemptStore(tmp_path / "transactions")
    )
    monkeypatch.setattr(
        cli,
        "ConsentManager",
        lambda store, **_: ConsentManager(
            store, interactive=interactive, prompt=confirm
        ),
    )
    client = PlaywrightClient()
    client.page, client.context = page, page.context

    async def login():
        login_calls.append(True)  # user has already completed synthetic ready

    def runner():
        # Exercise the actual CLI service assembly including its mode gate.
        obj = cli.build_runner(config, client, reporter=reporter)
        obj.session_service.prompt_enter = lambda _: None

        async def local_member_transport():
            # APIRequestContext bypasses browser route interception. Use the routed
            # doctor's synthetic table as member transport, keeping the real parser.
            return obj.session_service.parse_member_profiles(await page.content())

        obj.session_service.fetch_member_profiles = local_member_transport
        obj.auth_service.ensure_login = login
        return obj

    handle = raw_logger.add(lambda message: logs.append(str(message)))
    try:
        result = await runner().run()
        accepted = consent in {"accept", "recover"}
        polls = 2 if consent == "recover" else 1
        assert result.state == (
            "OUTCOME_UNKNOWN" if accepted else "AWAITING_MANUAL_CONFIRMATION"
        )
        assert result.exit_code == (3 if accepted else 2)
        assert result.success is False
        assert counts == {"poll": polls, "booking": 1, "click": int(accepted)}
        assert await page.locator('input[name="mid"]').is_checked()
        assert await page.locator("#delts li").get_attribute("class") == "selected"
        assert await page.locator("#hismemid").input_value() == CANARIES[5]
        assert await page.locator("textarea").input_value() == POISON
        assert len(prompts) == (1 if consent in {"accept", "recover", "reject"} else 0)
        if accepted:
            assert store.pending()
            # New objects + released/reacquired OS leader must stop before login,
            # poll or a second slot; revoke is not reconciliation.
            async with leader_scope():
                store.revoke()
            assert (await runner().run()).state == "OUTCOME_UNKNOWN"
            assert len(login_calls) == polls
            assert counts == {"poll": polls, "booking": 1, "click": 1}
            attempt = store.pending()[0]
            async with leader_scope():
                store.resolve(attempt["attempt_id"], booked=False)
            assert not store.pending()
            assert store.read()["audit"][-1]["state"] == "CONFIRMED_NO_EFFECT"
            # Resolution permits a fresh manual run, never an automatic re-click
            # of the submitted booking. Fresh run-only consent is required.
            await page.goto(DOCTOR)
            interactive = False
            assert (await runner().run()).state == "AWAITING_MANUAL_CONFIRMATION"
            assert counts == {"poll": polls + 1, "booking": 2, "click": 1}
        await reporter.emit_event(
            "run_finished",
            message=POISON,
            data={
                "state": result.state,
                "member_id": CANARIES[3],
                "detail": {"value": POISON},
            },
            notify=True,
        )
        assert notifications
        assert "notification_delivery_failed" in sink.path.read_text()
        assert_clean([logs, notifications, sink.path.read_text(), store.read()])
        assert not unexpected
    finally:
        raw_logger.remove(handle)


@pytest.mark.parametrize("booked", [False, True])
async def test_userscript_doctor_handoff_unknown_stop_reload_and_human_resolution(
    chromium_page,
    booked,
):
    page = chromium_page
    counts, unexpected = await install_site(page, userscript=True)
    prompts, console = [], []
    page.on("console", lambda message: console.append(message.text))

    async def accept(dialog):
        prompts.append(dialog.message)
        await dialog.accept()

    page.on("dialog", accept)
    await page.evaluate(f"""() => {{
      const h = {HOOKS};
      h.writeSettings({{member:{{memberId:'{CANARIES[3]}'}},
        filters:{{hours:['9-9.5'],startDate:'2030-01-02'}},
        booking:{{clinicCard:'{CANARIES[5]}',diseaseDescription:'{POISON}'}},
        pacing:{{pageActionMs:[0,0],pollMs:[3000,3000]}}}});
      h.startRun();
    }}""")
    await page.wait_for_url(BOOKING)
    result = await page.evaluate(f"""async () => {{
      const h = {HOOKS}; const id = h.claimPageController('booking');
      await h.runBookingPageController(id);
      return {{state:h.readState().outcome,running:h.readState().running,
        beforeClick:JSON.parse(window.clickJournal),journal:h.readJournal()}};
    }}""")
    assert result["state"] == "OUTCOME_UNKNOWN" and result["running"] is False
    assert counts == {"poll": 1, "booking": 1, "click": 1}
    assert result["beforeClick"]["attempts"][0]["state"] == "SUBMITTING"
    assert await page.locator('input[name="mid"]').is_checked()
    assert await page.locator("#delts li").get_attribute("class") == "selected"
    assert await page.locator("#hismemid").input_value() == CANARIES[5]
    assert await page.locator("textarea").input_value() == POISON
    assert len(prompts) == 1 and CANARIES[3] in prompts[0]
    assert_clean([console, result["journal"], result["beforeClick"]])
    await page.evaluate(
        f"{HOOKS}.stopRun(); {HOOKS}.resetRuntimeState(); {HOOKS}.revokeConsent()"
    )
    await page.reload()
    await page.evaluate(f"{HOOKS}.startRun()")
    assert await page.evaluate(f"{HOOKS}.submissionBlocked()") is True
    assert counts["click"] == 1 and counts["poll"] == 1
    assert await page.evaluate("localStorage.getItem('syntheticClicks')") == "1"
    await page.evaluate(f"{HOOKS}.resolvePending({str(booked).lower()})")
    journal = await page.evaluate(f"{HOOKS}.readJournal()")
    assert not await page.evaluate(f"{HOOKS}.submissionBlocked()")
    expected = "CONFIRMED_SUCCESS" if booked else "CONFIRMED_NO_EFFECT"
    assert journal["audit"][-1]["state"] == expected
    assert journal["attempts"][0]["evidence_type"] == "human_verified"
    assert await page.evaluate(f"{HOOKS}.readState().running") is False
    assert counts["click"] == 1
    assert_clean([console, journal, await page.evaluate(f"{HOOKS}.readState().logs")])
    assert not unexpected
