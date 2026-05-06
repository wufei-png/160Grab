import pytest

from main import parse_args


def test_parse_args_supports_create_profile_mode():
    args = parse_args(["config.yaml", "--create-profile", "--profile-name", "alpha"])

    assert args.config_path == "config.yaml"
    assert args.create_profile is True
    assert args.profile_name == "alpha"


def test_parse_args_rejects_profile_name_without_create_profile():
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["config.yaml", "--profile-name", "alpha"])

    assert excinfo.value.code == 2
