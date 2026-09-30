"""Wall-clock instants are aware; naive legacy values use the configured zone."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def schedule_instant(value: datetime, timezone: str) -> datetime:
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(UTC)
    zone = ZoneInfo(timezone)
    candidates = set()
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if candidate.astimezone(zone).replace(tzinfo=None) == value:
            candidates.add(candidate)
    if len(candidates) != 1:
        raise ValueError(
            "Schedule time is ambiguous or nonexistent; use an offset ISO time"
        )
    return candidates.pop()
