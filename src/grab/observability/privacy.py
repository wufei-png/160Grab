"""Closed output schemas. Free text and recursive values never cross a sink."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

EVENTS = frozenset(
    [
        "booking_form_opened",
        "booking_submit_failed",
        "booking_succeeded",
        "diagnostic_event",
        "notification_delivery_failed",
        "login_page_diagnostics",
        "manual_login_waiting",
        "member_resolved",
        "rate_limit_detected",
        "rate_limit_threshold_reached",
        "run_failed",
        "run_finished",
        "run_started",
        "schedule_poll_completed",
        "schedule_poll_heartbeat",
        "scheduler_wait_started",
        "session_keepalive_completed",
        "session_recovery_required",
        "snapshot_saved",
        "target_capture_failed",
        "target_captured",
        "target_resolution_failed",
    ]
)
PHASES = frozenset(
    {
        "startup",
        "manual_login",
        "capture_target",
        "resolve_target",
        "resolve_member",
        "wait_until_ready",
        "schedule_polling",
        "booking",
        "session_recovery",
        "shutdown",
    }
)
LEVELS = frozenset({"debug", "info", "warning", "error", "critical"})
STATES = frozenset(
    {
        "DISCOVERED",
        "PREPARED",
        "AWAITING_MANUAL_CONFIRMATION",
        "SUBMITTING",
        "CONFIRMED_SUCCESS",
        "CONFIRMED_NO_EFFECT",
        "OUTCOME_UNKNOWN",
    }
)
COUNTS = frozenset(
    {
        "attempt",
        "attempts",
        "consecutive_hits",
        "slot_count",
        "poll_attempt",
        "delay_ms",
        "elapsed_seconds",
        "remaining_seconds",
        "cooldown_ms",
        "matching_slots",
        "next_poll_delay_ms",
        "captcha_iframe_count",
        "cookie_count",
        "status",
        "event_count",
    }
)
FLAGS = frozenset(
    {
        "success",
        "ready",
        "ticket_present",
        "randstr_present",
        "desktop_notifications",
        "webhook_enabled",
        "needs_resolution",
        "human_action_required",
    }
)
FAILURES = frozenset(
    {
        "timeout",
        "network",
        "rate_limited",
        "session_expired",
        "storage",
        "notification",
        "unknown",
    }
)
PROVIDERS = frozenset(
    {"desktop:windows", "desktop:macos", "desktop:unknown", "webhook:http"}
)


def opaque_ref(value: Any) -> str | None:
    return (
        value
        if isinstance(value, str) and re.fullmatch(r"[a-f0-9]{12}|[a-f0-9]{32}", value)
        else None
    )


def safe_data(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, item in value.items():
        if key in COUNTS and type(item) in (int, float) and 0 <= item <= 1_000_000_000:
            result[key] = item
        elif key in FLAGS and type(item) is bool:
            result[key] = item
        elif key in {"run_id", "attempt_id", "booking_ref"} and opaque_ref(item):
            result[key] = item
        elif key == "state" and isinstance(item, str) and item in STATES:
            result[key] = item
        elif key == "failure_class" and isinstance(item, str) and item in FAILURES:
            result[key] = item
        elif key == "provider" and isinstance(item, str) and item in PROVIDERS:
            result[key] = item
        elif key == "original_event" and isinstance(item, str) and item in EVENTS:
            result[key] = item
    return result


def safe_event(value: Any) -> str:
    return value if isinstance(value, str) and value in EVENTS else "diagnostic_event"


def safe_phase(value: Any) -> str:
    return value if isinstance(value, str) and value in PHASES else "startup"


def safe_level(value: Any) -> str:
    return value if isinstance(value, str) and value in LEVELS else "info"


def event_message(event: str) -> str:
    return {
        "run_started": "Run started.",
        "run_finished": "Run finished.",
        "run_failed": "Run failed.",
        "booking_succeeded": "Booking succeeded.",
        "manual_login_waiting": "请在浏览器中手动完成登录，并导航到目标医生页。",
        "notification_delivery_failed": "Notification delivery failed.",
    }.get(safe_event(event), safe_event(event).replace("_", " ").capitalize() + ".")


def project_event(payload: Any) -> dict[str, Any]:
    value = payload if isinstance(payload, dict) else {}
    event = safe_event(value.get("event"))
    return {
        "ts": datetime.now(UTC).isoformat(),
        "run_id": opaque_ref(value.get("run_id")),
        "event": event,
        "phase": safe_phase(value.get("phase")),
        "level": safe_level(value.get("level")),
        "message": event_message(event),
        "data": safe_data(value.get("data")),
    }


def notification_projection(payload: Any) -> dict[str, Any]:
    value = payload if isinstance(payload, dict) else {}
    event = project_event({**value, "level": value.get("level", value.get("severity"))})
    result = {key: event[key] for key in ("event", "run_id", "phase", "message")}
    result["severity"] = event["level"]
    value = payload if isinstance(payload, dict) else {}
    attempt = opaque_ref(value.get("attempt_id")) or event["data"].get("attempt_id")
    if attempt:
        result["attempt_id"] = attempt
    return result


def notification_message(value: Any) -> str:
    allowed = {event_message(event) for event in EVENTS}
    return (
        value
        if isinstance(value, str) and value in allowed
        else event_message("diagnostic_event")
    )
