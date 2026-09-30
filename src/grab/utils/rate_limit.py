from __future__ import annotations

from collections.abc import Iterable
from typing import Any

RATE_LIMIT_PATTERNS = (
    "单位时间内访问次数过多",
    "访问次数过多",
    "访问过于频繁",
    "操作过于频繁",
)


class RateLimitError(RuntimeError):
    def __init__(self, message: str, context: str, *, retry_after: float = 0):
        self.retry_after = retry_after
        self.message = message
        self.context = context
        super().__init__(f"{context}: {message}")


def extract_rate_limit_message(payload: Any) -> str | None:
    for text in _iter_texts(payload):
        compact = " ".join(text.split())
        if not compact:
            continue
        for pattern in RATE_LIMIT_PATTERNS:
            if pattern in compact:
                return _extract_snippet(compact, pattern)
    return None


def raise_if_rate_limited(payload: Any, context: str) -> None:
    message = extract_rate_limit_message(payload)
    if message is not None:
        raise RateLimitError(message=message, context=context)


def _iter_texts(payload: Any) -> Iterable[str]:
    if isinstance(payload, str):
        yield payload
        return
    if isinstance(payload, dict):
        for value in payload.values():
            yield from _iter_texts(value)
        return
    if isinstance(payload, (list, tuple, set)):
        for item in payload:
            yield from _iter_texts(item)


def _extract_snippet(text: str, pattern: str, radius: int = 48) -> str:
    index = text.find(pattern)
    if index < 0:
        return pattern
    start = max(0, index - radius)
    end = min(len(text), index + len(pattern) + radius)
    return text[start:end]


def parse_retry_after(value, now=None) -> float:
    """HTTP delta-seconds or date; invalid hints never shorten local cooldown."""
    import math
    from datetime import UTC, datetime
    from email.utils import parsedate_to_datetime

    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            instant = parsedate_to_datetime(str(value))
            seconds = (instant - (now or datetime.now(UTC))).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 0
    return max(0, seconds) if math.isfinite(seconds) else 0
