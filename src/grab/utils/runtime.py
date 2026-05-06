import random
import re


def parse_sleep_time(value: str) -> int:
    if "-" not in value:
        return int(value)

    start, end = value.split("-", maxsplit=1)
    return random.randint(int(start), int(end))


def _normalize_hour_endpoint(value: str) -> str:
    value = str(value).strip()

    integer_match = re.fullmatch(r"(\d{1,2})", value)
    if integer_match:
        return f"{int(integer_match.group(1)):02d}:00"

    half_hour_match = re.fullmatch(r"(\d{1,2})\.(0|5)", value)
    if half_hour_match:
        hour, decimal = half_hour_match.groups()
        minute = "00" if decimal == "0" else "30"
        return f"{int(hour):02d}:{minute}"

    precise_match = re.fullmatch(r"(\d{1,2}):(\d{2})", value)
    if precise_match:
        hour, minute = precise_match.groups()
        hour_int = int(hour)
        minute_int = int(minute)
        if not (0 <= hour_int <= 23 and 0 <= minute_int <= 59):
            raise ValueError
        return f"{hour_int:02d}:{minute_int:02d}"

    raise ValueError


def normalize_hour_value(value: str) -> str:
    text = str(value).strip()
    if "-" not in text:
        raise ValueError(
            "Invalid hour format. Use HH:MM-HH:MM, H-H, H.5-H, or mixed variants like 9:30-10."
        )

    start_text, end_text = text.split("-", maxsplit=1)
    try:
        start = _normalize_hour_endpoint(start_text)
        end = _normalize_hour_endpoint(end_text)
    except ValueError as exc:
        raise ValueError(
            "Invalid hour format. Use HH:MM-HH:MM, H-H, H.5-H, or mixed variants like 9:30-10."
        ) from exc

    return f"{start}-{end}"
