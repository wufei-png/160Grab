import json
from types import SimpleNamespace

import pytest
from loguru import logger as raw_logger

from grab.models.schemas import GrabConfig
from grab.observability.privacy import project_event, safe_data
from grab.observability.reporter import JsonlEventSink, RunReporter
from grab.observability.safe_logging import logger
from grab.services.session import SessionCaptureService

# Deliberately synthetic canaries; never copied from a real account.
CANARIES = [
    "SYN_NAME_Q",
    "SYN_CERT_Q",
    "SYN_PHONE_Q",
    "SYN_MEMBER_Q",
    "SYN_TOKEN_Q",
    "SYN_CARD_Q",
    "SYN_ADDRESS_Q",
]
POISON = "|".join(CANARIES)


def assert_clean(value):
    text = json.dumps(value, ensure_ascii=False, default=str)
    assert all(token not in text for token in CANARIES)


def test_closed_projection_drops_recursive_and_free_text():
    payload = project_event(
        {
            "event": POISON,
            "phase": POISON,
            "level": POISON,
            "message": POISON,
            "run_id": POISON,
            "traceback": POISON,
            "data": {
                "member_id": POISON,
                "ready": POISON,
                "selector": {"value": POISON},
                "url": "https://example.test/?token=" + POISON,
                "status": {"nested": POISON},
                "unknown": True,
                "success": True,
                "attempt": 2,
            },
        }
    )
    assert_clean(payload)
    assert payload["data"] == {"success": True, "attempt": 2}
    assert safe_data({"attempt": float("inf"), "status": True, "ready": 1}) == {}


@pytest.mark.asyncio
async def test_jsonl_and_direct_logger_do_not_render_exception_or_values(tmp_path):
    captured = []
    handle = raw_logger.add(lambda message: captured.append(str(message)))

    class Notifications:
        async def notify(self, **kwargs):
            return []

    try:
        sink = JsonlEventSink(tmp_path, "a" * 12)
        reporter = RunReporter(
            sink=sink, notification_manager=Notifications(), rate_limit_threshold=2
        )
        await reporter.emit_event(
            "run_failed",
            message=POISON,
            data={
                "message": POISON,
                "exception": ValueError(POISON),
                "detail": {"selector": POISON, "value": POISON},
            },
        )
        logger.exception(POISON, exception=ValueError(POISON))
        logger.info("Resolved member_id=[omitted]", POISON)
    finally:
        raw_logger.remove(handle)
    assert_clean(captured)
    assert_clean(sink.path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_login_diagnostic_stdout_and_reporter_drop_page_text(capsys):
    class Reporter:
        events = []

        async def emit_event(self, event, **kwargs):
            self.events.append(kwargs)

    async def state():
        return {
            "events": [{"kind": "pageerror", "text": POISON, "url": POISON}],
            "login_form": {
                "visible_messages": [POISON],
                "target_value": POISON,
                "ticket_present": POISON,
                "randstr_present": True,
            },
        }

    reporter = Reporter()
    service = SessionCaptureService(
        page=SimpleNamespace(url="about:blank"),
        config=GrabConfig(),
        debug_state_provider=state,
        reporter=reporter,
    )
    await service.print_login_page_diagnostics()
    assert_clean(capsys.readouterr().out)
    assert_clean(reporter.events)


@pytest.mark.asyncio
async def test_notifications_capture_only_projection_and_failure_is_safe(tmp_path):
    from grab.observability.notifications import (
        HttpWebhookNotifier,
        NotificationManager,
    )

    desktop, webhook = [], []

    class Desktop:
        provider_name = "desktop:macos"

        async def notify(self, **kwargs):
            desktop.append(kwargs)
            raise RuntimeError(POISON)

    async def post(url, body, timeout, headers):
        webhook.append(body)
        raise RuntimeError("https://example.test/?token=" + POISON)

    manager = NotificationManager(
        desktop_notifier=Desktop(),
        webhook_notifier=HttpWebhookNotifier(
            url="https://example.test/hook", timeout_seconds=1, post_json=post
        ),
    )
    sink = JsonlEventSink(tmp_path, "a" * 12)
    reporter = RunReporter(
        sink=sink, notification_manager=manager, rate_limit_threshold=2
    )
    event = await reporter.emit_event(
        "booking_succeeded",
        message=POISON,
        notify=True,
        notification_title=POISON,
        notification_subtitle=POISON,
        data={"token": POISON, "success": True, "url": POISON},
    )
    assert event["event"] == "booking_succeeded"
    assert_clean([desktop, webhook, sink.path.read_text(encoding="utf-8")])
    assert set(webhook[0]) == {"event", "run_id", "phase", "message", "severity"}
    assert desktop[0]["title"] == "160Grab"
    assert desktop[0]["subtitle"] is None
    assert webhook[0]["message"] == "Booking succeeded."
    assert "notification_delivery_failed" in sink.path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_broken_notification_manager_cannot_change_booking_event(tmp_path):
    class Broken:
        async def notify(self, **kwargs):
            raise RuntimeError(POISON)

    sink = JsonlEventSink(tmp_path, "a" * 12)
    reporter = RunReporter(
        sink=sink, notification_manager=Broken(), rate_limit_threshold=2
    )
    result = await reporter.emit_event("booking_succeeded", message=POISON, notify=True)
    assert result["event"] == "booking_succeeded"
    assert_clean(sink.path.read_text(encoding="utf-8"))
