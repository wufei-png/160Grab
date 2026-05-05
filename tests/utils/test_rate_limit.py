import pytest

from grab.utils.rate_limit import (
    RateLimitError,
    extract_rate_limit_message,
    raise_if_rate_limited,
)


def test_extract_rate_limit_message_finds_nested_payload_message():
    payload = {"data": {"tips": ["其他提示", "您单位时间内访问次数过多！"]}}

    message = extract_rate_limit_message(payload)

    assert message is not None
    assert "访问次数过多" in message


def test_raise_if_rate_limited_raises_contextual_error():
    with pytest.raises(RateLimitError, match="booking submit page"):
        raise_if_rate_limited(
            "<html><body>操作过于频繁，请稍后再试</body></html>",
            context="booking submit page",
        )
