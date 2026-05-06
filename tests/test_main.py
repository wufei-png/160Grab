from argparse import Namespace
from pathlib import Path

import pytest

from main import ensure_frozen_default_config, parse_args, resolve_config_path


def test_parse_args_supports_create_profile_mode():
    args = parse_args(["config.yaml", "--create-profile", "--profile-name", "alpha"])

    assert args.config_path == "config.yaml"
    assert args.create_profile is True
    assert args.profile_name == "alpha"


def test_parse_args_rejects_profile_name_without_create_profile():
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["config.yaml", "--profile-name", "alpha"])

    assert excinfo.value.code == 2


def test_parse_args_supports_smoke_browser_mode():
    args = parse_args(["--smoke-browser"])

    assert args.smoke_browser is True
    assert args.config_path is None


def test_resolve_config_path_defaults_to_repo_config_in_source_mode():
    args = Namespace(config_path=None)

    config_path, explicit = resolve_config_path(args, frozen=False)

    assert config_path == Path("config.yaml")
    assert explicit is False


def test_resolve_config_path_defaults_to_executable_dir_in_frozen_mode(tmp_path):
    args = Namespace(config_path=None)

    config_path, explicit = resolve_config_path(
        args,
        frozen=True,
        executable=tmp_path / "160Grab",
    )

    assert config_path == tmp_path / "config.yaml"
    assert explicit is False


def test_resolve_config_path_preserves_explicit_value():
    args = Namespace(config_path="~/custom.yaml")

    config_path, explicit = resolve_config_path(args, frozen=True)

    assert config_path == Path("~/custom.yaml").expanduser()
    assert explicit is True


def test_ensure_frozen_default_config_copies_template(tmp_path):
    template_path = tmp_path / "example.yaml"
    template_path.write_text("auth:\n  strategy: manual\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    messages: list[str] = []

    created = ensure_frozen_default_config(
        config_path,
        template_path=template_path,
        output=messages.append,
    )

    assert created is True
    assert config_path.read_text(encoding="utf-8") == template_path.read_text(
        encoding="utf-8"
    )
    assert "config.yaml" in messages[0]


def test_ensure_frozen_default_config_is_noop_when_config_exists(tmp_path):
    template_path = tmp_path / "example.yaml"
    template_path.write_text("template\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("existing\n", encoding="utf-8")

    created = ensure_frozen_default_config(
        config_path,
        template_path=template_path,
    )

    assert created is False
    assert config_path.read_text(encoding="utf-8") == "existing\n"
