import pytest

from grab.utils.runtime import normalize_hour_value, parse_sleep_time


def test_parse_sleep_time_range_returns_bounded_value():
    delay = parse_sleep_time("3000-5000")

    assert 3000 <= delay <= 5000


def test_parse_sleep_time_fixed_value_returns_exact_delay():
    assert parse_sleep_time("3000") == 3000


def test_normalize_hour_value_supports_compact_integer_ranges():
    assert normalize_hour_value("8-9") == "08:00-09:00"
    assert normalize_hour_value("18-18") == "18:00-18:00"


def test_normalize_hour_value_preserves_precise_format():
    assert normalize_hour_value("08:00-08:30") == "08:00-08:30"
    assert normalize_hour_value("9:30-10") == "09:30-10:00"
    assert normalize_hour_value("9-9:30") == "09:00-09:30"


def test_normalize_hour_value_supports_half_hour_decimal_format():
    assert normalize_hour_value("9-9.5") == "09:00-09:30"
    assert normalize_hour_value("9.5-10") == "09:30-10:00"
    assert normalize_hour_value("9.5-19") == "09:30-19:00"


def test_normalize_hour_value_rejects_invalid_format():
    with pytest.raises(ValueError):
        normalize_hour_value("9.25-10")
