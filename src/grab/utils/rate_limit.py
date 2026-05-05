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
    def __init__(self, message: str, context: str):
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
