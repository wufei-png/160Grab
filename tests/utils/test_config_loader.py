import pytest
from pydantic import ValidationError

from grab.utils.config_loader import load_config


def test_load_config_supports_manual_mode_defaults_and_filters(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
member_id: "m1"
weeks: [1, 3, 5]
days: ["am"]
hours: ["8-9", "9.5-10", "9:30-10"]
sleep_time: "3000-5000"
brush_start_date: "2026-03-24"
enable_appoint: true
appoint_time: "2026-03-24 08:00:00"
booking_strategy: "page"
auth:
  strategy: "manual"
browser:
  stealth: false
""".strip()
    )

    config = load_config(config_file)

    assert config.member_id == "m1"
    assert config.hours == ["08:00-09:00", "09:30-10:00", "09:30-10:00"]
    assert config.sleep_time == "3000-5000"
    assert config.enable_appoint is True
    assert config.booking_strategy == "page"
    assert config.auth.strategy == "manual"
    assert config.browser.stealth is False


def test_load_config_allows_missing_member_id_for_prompted_selection(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
weeks: [2]
days: ["pm"]
hours: []
auth:
  strategy: "manual"
""".strip()
    )

    config = load_config(config_file)

    assert config.member_id is None
    assert config.browser.stealth is True


def test_load_config_rejects_invalid_hour_format(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
hours: ["9.25-10"]
auth:
  strategy: "manual"
""".strip()
    )

    with pytest.raises(ValidationError):
        load_config(config_file)
