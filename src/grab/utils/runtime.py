import random
import re


def parse_sleep_time(value: str) -> int:
    if "-" not in value:
        return int(value)

    start, end = value.split("-", maxsplit=1)
    return random.randint(int(start), int(end))


def normalize_hour_value(value: str) -> str:
    compact_match = re.fullmatch(r"(\d{1,2})-(\d{1,2})", value)
    if compact_match:
        start, end = compact_match.groups()
        return f"{int(start):02d}:00-{int(end):02d}:00"

    precise_match = re.fullmatch(r"(\d{2}:\d{2})-(\d{2}:\d{2})", value)
    if precise_match:
        return value

    raise ValueError(
        "Invalid hour format. Use HH:MM-HH:MM or compact H-H integer ranges."
    )
