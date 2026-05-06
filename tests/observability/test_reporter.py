import json
from pathlib import Path

import pytest

import grab.observability.reporter as reporter_module
from grab.models.schemas import GrabConfig
from grab.observability.notifications import NotificationDeliveryResult
from grab.observability.reporter import JsonlEventSink, RunReporter


class FakeNotificationManager:
    def __init__(self, results=None):
        self.results = results or []
        self.calls: list[dict] = []

    async def notify(
        self,
        *,
        title: str,
        message: str,
        severity: str,
        payload: dict,
        subtitle: str | None = None,
    ):
        self.calls.append(
            {
                "title": title,
                "message": message,
                "severity": severity,
                "payload": payload,
                "subtitle": subtitle,
            }
        )
        return list(self.results)


class BrokenSink:
    def __init__(self, path: str = "/tmp/broken-run.jsonl"):
        self.path = Path(path)
        self.calls = 0

    def write(self, payload: dict) -> None:
        del payload
        self.calls += 1
        raise OSError("disk full")


@pytest.mark.asyncio
async def test_reporter_writes_structured_jsonl_events(tmp_path):
    sink = JsonlEventSink(tmp_path, run_id="run123")
    reporter = RunReporter(
        sink=sink,
        notification_manager=FakeNotificationManager(),
        rate_limit_threshold=3,
        run_id="run123",
    )

    await reporter.emit_event(
        "run_started",
        level="info",
        message="Run started.",
        data={"config_path": "config.yaml"},
    )

    lines = sink.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event"] == "run_started"
    assert payload["run_id"] == "run123"
    assert payload["phase"] == "startup"
    assert payload["message"] == "Run started."
    assert payload["data"] == {"config_path": "config.yaml"}


@pytest.mark.asyncio
async def test_reporter_records_notification_delivery_failures(tmp_path):
    sink = JsonlEventSink(tmp_path, run_id="run123")
    reporter = RunReporter(
        sink=sink,
        notification_manager=FakeNotificationManager(
            results=[
                NotificationDeliveryResult(
                    provider="desktop:windows",
                    ok=False,
                    error="toast failed",
                )
            ]
        ),
        rate_limit_threshold=3,
        run_id="run123",
    )

    await reporter.emit_event(
        "booking_succeeded",
        level="info",
        message="Booking succeeded.",
        notify=True,
        notification_title="160Grab 挂号成功",
    )

    payloads = [
        json.loads(line) for line in sink.path.read_text(encoding="utf-8").splitlines()
    ]
    assert [payload["event"] for payload in payloads] == [
        "booking_succeeded",
        "notification_delivery_failed",
    ]
    assert payloads[1]["data"]["provider"] == "desktop:windows"
    assert payloads[1]["data"]["original_event"] == "booking_succeeded"


@pytest.mark.asyncio
async def test_reporter_rate_limit_threshold_notifies_once_until_reset(tmp_path):
    notifications = FakeNotificationManager()
    sink = JsonlEventSink(tmp_path, run_id="run123")
    reporter = RunReporter(
        sink=sink,
        notification_manager=notifications,
        rate_limit_threshold=2,
        run_id="run123",
    )

    await reporter.record_rate_limit(context="schedule_polling", message="访问次数过多")
    await reporter.record_rate_limit(context="schedule_polling", message="访问次数过多")
    await reporter.record_rate_limit(context="schedule_polling", message="访问次数过多")

    assert len(notifications.calls) == 1
    assert notifications.calls[0]["title"] == "160Grab 持续限频"

    reporter.reset_rate_limit_streak()

    await reporter.record_rate_limit(context="booking", message="访问次数过多")
    await reporter.record_rate_limit(context="booking", message="访问次数过多")

    assert len(notifications.calls) == 2
    payloads = [
        json.loads(line) for line in sink.path.read_text(encoding="utf-8").splitlines()
    ]
    threshold_events = [
        payload for payload in payloads if payload["event"] == "rate_limit_threshold_reached"
    ]
    assert len(threshold_events) == 2


@pytest.mark.asyncio
async def test_reporter_disables_sink_after_write_failure_and_keeps_notifying():
    notifications = FakeNotificationManager()
    sink = BrokenSink()
    reporter = RunReporter(
        sink=sink,
        notification_manager=notifications,
        rate_limit_threshold=3,
        run_id="run123",
    )

    await reporter.emit_event(
        "booking_succeeded",
        level="info",
        message="Booking succeeded.",
        notify=True,
        notification_title="160Grab 挂号成功",
    )
    await reporter.emit_event(
        "run_finished",
        level="info",
        message="Run finished.",
    )

    assert sink.calls == 1
    assert reporter.sink is None
    assert len(notifications.calls) == 1


@pytest.mark.asyncio
async def test_build_run_reporter_falls_back_when_sink_init_fails(monkeypatch):
    class RaisingJsonlEventSink:
        def __init__(self, root_dir, run_id):
            del root_dir, run_id
            raise OSError("read-only file system")

    monkeypatch.setattr(reporter_module, "JsonlEventSink", RaisingJsonlEventSink)

    reporter = reporter_module.build_run_reporter(GrabConfig())

    assert reporter.jsonl_path is None
    await reporter.emit_event(
        "run_started",
        level="info",
        message="Run started.",
    )
