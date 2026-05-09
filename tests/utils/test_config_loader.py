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
  launch_persistent_context: false
  profile_name: "profile_7"
  profiles_root_dir: "~/custom-profiles"
  session_refresh_interval_seconds: 180
  session_recovery_max_attempts: 5
  session_recovery_cooldown_seconds: 12
logging:
  jsonl_dir: "~/logs"
  heartbeat_interval_seconds: 120
notifications:
  desktop: false
  rate_limit_threshold: 5
  webhook:
    url: "https://example.com/hook"
    timeout_seconds: 8
    headers:
      X-Test: "1"
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
    assert config.browser.launch_persistent_context is False
    assert config.browser.profile_name == "profile_7"
    assert config.browser.profiles_root_dir == "~/custom-profiles"
    assert config.browser.session_refresh_interval_seconds == 180
    assert config.browser.session_recovery_max_attempts == 5
    assert config.browser.session_recovery_cooldown_seconds == 12
    assert config.logging.jsonl_dir == "~/logs"
    assert config.logging.heartbeat_interval_seconds == 120
    assert config.notifications.desktop is False
    assert config.notifications.rate_limit_threshold == 5
    assert config.notifications.webhook.url == "https://example.com/hook"
    assert config.notifications.webhook.timeout_seconds == 8
    assert config.notifications.webhook.headers == {"X-Test": "1"}


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
    assert config.browser.launch_persistent_context is True
    assert config.browser.profile_name is None
    assert config.browser.profiles_root_dir == "~/.160grab/browser-profiles"
    assert config.browser.session_refresh_interval_seconds == 240
    assert config.browser.session_recovery_max_attempts == 3
    assert config.browser.session_recovery_cooldown_seconds == 30
    assert config.logging.jsonl_dir == "~/.160grab/logs"
    assert config.logging.heartbeat_interval_seconds == 300
    assert config.notifications.desktop is True
    assert config.notifications.rate_limit_threshold == 3
    assert config.notifications.webhook.url is None
    assert config.notifications.webhook.timeout_seconds == 5
    assert config.notifications.webhook.headers == {}


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


def test_load_config_rejects_invalid_profile_name(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
browser:
  profile_name: "bad name"
auth:
  strategy: "manual"
""".strip()
    )

    with pytest.raises(ValidationError):
        load_config(config_file)


def test_load_config_rejects_negative_session_refresh_interval(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
browser:
  session_refresh_interval_seconds: -1
auth:
  strategy: "manual"
""".strip()
    )

    with pytest.raises(ValidationError):
        load_config(config_file)


def test_load_config_rejects_negative_session_recovery_max_attempts(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
browser:
  session_recovery_max_attempts: -1
auth:
  strategy: "manual"
""".strip()
    )

    with pytest.raises(ValidationError):
        load_config(config_file)


def test_load_config_rejects_negative_session_recovery_cooldown_seconds(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
browser:
  session_recovery_cooldown_seconds: -1
auth:
  strategy: "manual"
""".strip()
    )

    with pytest.raises(ValidationError):
        load_config(config_file)
