import json

import pytest

from grab.utils.profile_manager import (
    PROFILE_MARKER_FILENAME,
    create_profile,
    load_profile,
    resolve_profile_for_run,
)
from grab.utils.profile_name import validate_profile_name


def test_create_profile_auto_names_increment(tmp_path):
    first = create_profile(tmp_path)
    second = create_profile(tmp_path)

    assert first.name == "profile_1"
    assert second.name == "profile_2"
    assert first.marker_path.exists()
    assert second.marker_path.exists()


def test_create_profile_rejects_duplicate_name(tmp_path):
    create_profile(tmp_path, profile_name="demo")

    with pytest.raises(ValueError, match="already exists"):
        create_profile(tmp_path, profile_name="demo")


def test_validate_profile_name_rejects_unsafe_characters():
    with pytest.raises(ValueError, match="Invalid profile name"):
        validate_profile_name("bad name")


def test_load_profile_rejects_missing_marker(tmp_path):
    profile_dir = tmp_path / "demo"
    profile_dir.mkdir()

    with pytest.raises(ValueError, match="missing"):
        load_profile(tmp_path, "demo")


def test_resolve_profile_for_run_auto_detects_single_profile(tmp_path):
    profile = create_profile(tmp_path, profile_name="solo")
    messages: list[str] = []

    resolved = resolve_profile_for_run(
        root_dir=tmp_path,
        configured_profile_name=None,
        config_path=tmp_path / "config.yaml",
        prompt_text=lambda _message: "",
        notify=messages.append,
        is_interactive=True,
    )

    assert resolved.profile == profile
    assert resolved.source == "auto-detected"
    assert "唯一 profile" in messages[0]


def test_resolve_profile_for_run_auto_creates_first_profile_when_none_exist(tmp_path):
    messages: list[str] = []

    resolved = resolve_profile_for_run(
        root_dir=tmp_path,
        configured_profile_name=None,
        config_path=tmp_path / "config.yaml",
        prompt_text=lambda _message: "",
        notify=messages.append,
        is_interactive=True,
    )

    assert resolved.profile.name == "profile_1"
    assert resolved.source == "auto-created"
    assert resolved.profile.marker_path.exists()
    assert "自动创建" in messages[0]


def test_resolve_profile_for_run_prompts_for_selection_and_persists(tmp_path):
    create_profile(tmp_path, profile_name="alpha")
    target = create_profile(tmp_path, profile_name="beta")
    responses = iter(["2", "y"])
    persisted: list[tuple[str, str]] = []
    messages: list[str] = []

    resolved = resolve_profile_for_run(
        root_dir=tmp_path,
        configured_profile_name=None,
        config_path=tmp_path / "config.yaml",
        prompt_text=lambda _message: next(responses),
        notify=messages.append,
        is_interactive=True,
        persist_profile_name=lambda path, name: persisted.append((str(path), name)),
    )

    assert resolved.profile.name == target.name
    assert resolved.source == "selected"
    assert persisted == [(str(tmp_path / "config.yaml"), "beta")]
    assert any("多个可用 profile" in message for message in messages)


def test_resolve_profile_for_run_rejects_multi_profile_non_interactive(tmp_path):
    create_profile(tmp_path, profile_name="alpha")
    create_profile(tmp_path, profile_name="beta")

    with pytest.raises(ValueError, match="non-interactive"):
        resolve_profile_for_run(
            root_dir=tmp_path,
            configured_profile_name=None,
            config_path=tmp_path / "config.yaml",
            prompt_text=lambda _message: "",
            notify=lambda _message: None,
            is_interactive=False,
        )


def test_resolve_profile_for_run_reports_missing_configured_profile(tmp_path):
    create_profile(tmp_path, profile_name="alpha")

    with pytest.raises(ValueError, match="Available profiles: alpha"):
        resolve_profile_for_run(
            root_dir=tmp_path,
            configured_profile_name="missing",
            config_path=tmp_path / "config.yaml",
            prompt_text=lambda _message: "",
            notify=lambda _message: None,
            is_interactive=True,
        )


def test_create_profile_writes_expected_marker(tmp_path):
    profile = create_profile(tmp_path, profile_name="alpha")

    marker = json.loads(
        (profile.path / PROFILE_MARKER_FILENAME).read_text(encoding="utf-8")
    )
    assert marker["profile_name"] == "alpha"
    assert marker["version"] == 1
