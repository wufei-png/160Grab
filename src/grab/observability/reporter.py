from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from loguru import logger

from grab.observability.notifications import NotificationManager


class JsonlEventSink:
    def __init__(self, root_dir: str | Path, run_id: str):
        self.root_dir = Path(root_dir).expanduser()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        self.path = self.root_dir / f"{stamp}-{run_id}.jsonl"
        self.path.touch()

    def write(self, payload: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")


class RunReporter:
    def __init__(
        self,
        *,
        sink: JsonlEventSink | None,
        notification_manager: NotificationManager,
        rate_limit_threshold: int,
        run_id: str | None = None,
    ):
        self.sink = sink
        self.notification_manager = notification_manager
        self.rate_limit_threshold = rate_limit_threshold
        self.run_id = run_id or uuid4().hex[:12]
        self.current_phase = "startup"
        self._rate_limit_streak = 0
        self._rate_limit_notified = False

    @property
    def jsonl_path(self) -> Path | None:
        if self.sink is None:
            return None
        return self.sink.path

    def set_phase(self, phase: str) -> None:
        self.current_phase = phase

    def reset_rate_limit_streak(self) -> None:
        self._rate_limit_streak = 0
        self._rate_limit_notified = False

    async def emit_event(
        self,
        event: str,
        *,
        level: str = "info",
        message: str,
        data: dict[str, Any] | None = None,
        notify: bool = False,
        notification_title: str | None = None,
        notification_severity: str | None = None,
        notification_subtitle: str | None = None,
        phase: str | None = None,
    ) -> dict[str, Any]:
        payload = self._build_payload(
            event=event,
            level=level,
            message=message,
            data=data,
            phase=phase,
        )
        self._record(payload)

        if notify:
            await self._deliver_notifications(
                title=notification_title or "160Grab",
                message=message,
                severity=notification_severity or level,
                payload=payload,
                subtitle=notification_subtitle,
            )

        return payload

    async def record_rate_limit(
        self,
        *,
        context: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        self._rate_limit_streak += 1
        payload_data = {
            "context": context,
            "message": message,
            "consecutive_hits": self._rate_limit_streak,
        }
        if data:
            payload_data.update(data)

        await self.emit_event(
            "rate_limit_detected",
            level="warning",
            message=f"Rate limit detected during {context}: {message}",
            data=payload_data,
        )

        if (
            self._rate_limit_streak >= self.rate_limit_threshold
            and not self._rate_limit_notified
        ):
            self._rate_limit_notified = True
            await self.emit_event(
                "rate_limit_threshold_reached",
                level="warning",
                message=(
                    "Rate limit threshold reached; continuous rate limiting is"
                    f" happening during {context}."
                ),
                data=payload_data,
                notify=True,
                notification_title="160Grab 持续限频",
                notification_severity="warning",
            )

    async def record_snapshot(self, *, label: str, path: str | Path) -> None:
        await self.emit_event(
            "snapshot_saved",
            level="info",
            message=f"Saved diagnostic snapshot: {label}",
            data={"label": label, "path": str(path)},
        )

    async def _deliver_notifications(
        self,
        *,
        title: str,
        message: str,
        severity: str,
        payload: dict[str, Any],
        subtitle: str | None = None,
    ) -> None:
        results = await self.notification_manager.notify(
            title=title,
            message=message,
            severity=severity,
            payload=payload,
            subtitle=subtitle,
        )
        for result in results:
            if result.ok:
                continue
            failure_payload = self._build_payload(
                event="notification_delivery_failed",
                level="warning",
                message=f"Notification delivery failed via {result.provider}",
                data={
                    "provider": result.provider,
                    "error": result.error,
                    "original_event": payload.get("event"),
                },
                phase=payload.get("phase"),
            )
            self._record(failure_payload)

    def _record(self, payload: dict[str, Any]) -> None:
        self._log(payload["level"], payload["message"])
        if self.sink is None:
            return

        try:
            self.sink.write(payload)
        except Exception as exc:
            failed_path = self.sink.path
            self.sink = None
            logger.warning(
                "Structured event sink at {} failed and will be disabled: {}",
                failed_path,
                exc,
            )

    def _build_payload(
        self,
        *,
        event: str,
        level: str,
        message: str,
        data: dict[str, Any] | None,
        phase: str | None,
    ) -> dict[str, Any]:
        return {
            "ts": datetime.now(UTC).isoformat(),
            "run_id": self.run_id,
            "level": level,
            "event": event,
            "phase": phase or self.current_phase,
            "message": message,
            "data": _serialize_value(data or {}),
        }

    def _log(self, level: str, message: str) -> None:
        log_method = getattr(logger, level, logger.info)
        log_method(message)


def build_run_reporter(config) -> RunReporter:
    run_id = uuid4().hex[:12]
    sink = None
    try:
        sink = JsonlEventSink(config.logging.jsonl_dir, run_id=run_id)
    except Exception as exc:
        logger.warning(
            "Failed to initialize structured event sink at {}: {}. Continuing without JSONL event output.",
            config.logging.jsonl_dir,
            exc,
        )
    notification_manager = NotificationManager.from_config(config.notifications)
    return RunReporter(
        sink=sink,
        notification_manager=notification_manager,
        rate_limit_threshold=config.notifications.rate_limit_threshold,
        run_id=run_id,
    )


def _serialize_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_serialize_value(item) for item in value]
    return value
