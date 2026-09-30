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
    assert_clean(sink.path.read_text())


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
