"""Conservative read-only classifications; credentials alone never prove validity."""

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

from pydantic import ValidationError

from grab.errors import (
    SessionExpiredError,
    TransientSessionRefreshError,
    UnknownSessionError,
)
from grab.models.schemas import Slot
from grab.utils.rate_limit import RateLimitError, extract_rate_limit_message


class SessionState(StrEnum):
    VALID = "VALID"
    EXPIRED = "EXPIRED"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"
    RATE_LIMITED = "RATE_LIMITED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class SessionAssessment:
    state: SessionState
    failure_class: str | None = None

    def require_valid(self):
        if self.state == SessionState.EXPIRED:
            raise SessionExpiredError("Authenticated session expired.")
        if self.state == SessionState.TRANSIENT_FAILURE:
            raise TransientSessionRefreshError("Read-only transport failed.")
        if self.state == SessionState.RATE_LIMITED:
            raise RateLimitError("Rate limited.", "schedule_polling")
        if self.state == SessionState.UNKNOWN:
            raise UnknownSessionError(self.failure_class or "unknown")


def is_login_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.hostname in {"www.91160.com", "user.91160.com"}
        and parsed.path.rstrip("/") == "/login.html"
    )


def valid_schedule_tree(node, depth=0) -> bool:
    if not isinstance(node, dict) or depth > 16:
        return False
    if "schedule_id" in node or "y_state" in node:
        return (
            isinstance(node.get("schedule_id"), (str, int))
            and bool(str(node["schedule_id"]))
            and type(node.get("y_state")) is int
            and node["y_state"] in {-3, -2, -1, 0, 1}
        )
    return all(valid_schedule_tree(child, depth + 1) for child in node.values())


def classify_schedule(payload) -> SessionAssessment:
    if extract_rate_limit_message(payload):
        return SessionAssessment(SessionState.RATE_LIMITED, "rate_limited")
    if not isinstance(payload, dict):
        return SessionAssessment(SessionState.UNKNOWN, "schema_drift")
    # Only the already supported explicit business invalidation is trusted.
    if str(payload.get("error_code", "")) == "10021":
        return SessionAssessment(SessionState.EXPIRED, "session_expired")
    code = payload.get("result_code", payload.get("code"))
    if str(code) != "1":
        return SessionAssessment(SessionState.UNKNOWN, "unknown")
    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("schedules"), list):
        try:
            for item in data["schedules"]:
                slot = Slot.model_validate(item)
                if (
                    not slot.schedule_id
                    or not slot.doctor_id
                    or slot.weekday not in range(1, 8)
                    or slot.day_period not in {"am", "pm", "em"}
                    or slot.status
                    not in {
                        "available",
                        "full",
                        "expired",
                        "stopped",
                        "not_open",
                        "unavailable",
                    }
                ):
                    return SessionAssessment(SessionState.UNKNOWN, "schema_drift")
        except (ValidationError, TypeError):
            return SessionAssessment(SessionState.UNKNOWN, "schema_drift")
        return SessionAssessment(SessionState.VALID)
    if valid_schedule_tree(payload.get("sch")) and isinstance(
        payload.get("dates", {}), dict
    ):
        return SessionAssessment(SessionState.VALID)
    return SessionAssessment(SessionState.UNKNOWN, "schema_drift")
