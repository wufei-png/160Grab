import re

PROFILE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


def validate_profile_name(name: str) -> str:
    candidate = str(name).strip()
    if not candidate:
        raise ValueError("Profile name cannot be empty.")
    if PROFILE_NAME_PATTERN.fullmatch(candidate) is None:
        raise ValueError(
            "Invalid profile name. Use only letters, digits, '-' or '_'."
        )
    return candidate
