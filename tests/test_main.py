import io
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from loguru import logger

import main as main_module
from grab.models.schemas import RunResult
from main import (
    emit_console_message,
    ensure_frozen_default_config,
    parse_args,
    resolve_config_path,
)


@pytest.mark.parametrize("strategy", ["auto", "SYNTHETIC_INVALID_STRATEGY"])
@pytest.mark.parametrize("profile_args", [[], ["--create-profile"]])
async def test_invalid_auth_stops_before_browser_or_profile_creation(
    tmp_path, monkeypatch, strategy, profile_args
):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"auth:\n  strategy: {strategy}\n", encoding="utf-8")

    def unexpected(*args, **kwargs):
        pytest.fail(
            "Invalid auth must stop before any browser/profile/reporting action"
        )

    monkeypatch.setattr(main_module, "PlaywrightClient", unexpected)
    monkeypatch.setattr(main_module, "run_create_profile_flow", unexpected)
    monkeypatch.setattr(main_module, "build_run_reporter", unexpected)
    messages = []
    sink = logger.add(lambda message: messages.append(str(message)))
    try:
        with pytest.raises(SystemExit) as error:
            await main_module.main([str(config_path), *profile_args])
    finally:
        logger.remove(sink)
    assert error.value.code == 1
    assert "Invalid configuration" in "".join(messages)
    assert "SYNTHETIC_INVALID_STRATEGY" not in "".join(messages)


async def test_manual_cli_launches_visible_browser_and_runs(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "auth:\n  strategy: manual\nbrowser:\n  launch_persistent_context: false\n",
        encoding="utf-8",
    )
    launch_options = {}

    class Client:
        def __init__(self, **kwargs):
            launch_options.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    runner = SimpleNamespace(run=AsyncMock(return_value=RunResult(success=True)))
    reporter = SimpleNamespace(jsonl_path=None, emit_event=AsyncMock())
    monkeypatch.setattr(main_module, "PlaywrightClient", Client)
    monkeypatch.setattr(main_module, "build_run_reporter", lambda config: reporter)
    monkeypatch.setattr(main_module, "build_runner", lambda *args, **kwargs: runner)
    with pytest.raises(SystemExit) as error:
        await main_module.main([str(config_path)])
    assert error.value.code == 0
    assert launch_options["headless"] is False
    runner.run.assert_awaited_once()


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


def test_emit_console_message_falls_back_for_non_utf8_stream():
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict")

    emit_console_message("未找到 config.yaml", stream=stream)

    stream.flush()
    rendered = buffer.getvalue().decode("cp1252")
    assert "config.yaml" in rendered
    assert "\\u672a\\u627e\\u5230" in rendered


def test_ensure_frozen_default_config_default_output_handles_cp1252_stdout(
    tmp_path, monkeypatch
):
    template_path = tmp_path / "example.yaml"
    template_path.write_text("auth:\n  strategy: manual\n", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict")
    monkeypatch.setattr(main_module.sys, "stdout", stream)

    created = ensure_frozen_default_config(
        config_path,
        template_path=template_path,
    )

    stream.flush()
    rendered = buffer.getvalue().decode("cp1252")
    assert created is True
    assert config_path.exists()
    assert "config.yaml" in rendered
    assert "\\u8bf7\\u5148\\u6309\\u9700\\u4fee\\u6539" in rendered


async def test_cleanup_cli_dry_run_never_launches_or_resolves_profile(
    tmp_path, monkeypatch, capsys
):
    from tests.utils.test_retention import log_name

    root = tmp_path / "logs"
    root.mkdir()
    old = root / log_name(8)
    old.write_text("{}")
    config = tmp_path / "config.yaml"
    config.write_text(f"logging:\n  jsonl_dir: {root}\n")

    def unexpected(*args, **kwargs):
        pytest.fail("Cleanup must not launch a browser or read a profile")

    monkeypatch.setattr(main_module, "PlaywrightClient", unexpected)
    monkeypatch.setattr(main_module, "resolve_profile_for_run", unexpected)
    monkeypatch.setattr(main_module, "build_run_reporter", unexpected)
    with pytest.raises(SystemExit) as result:
        await main_module.main([str(config), "--cleanup-data", "--dry-run"])
    assert result.value.code == 0
    assert old.exists()
    assert "deleted=0" in capsys.readouterr().out


def test_cleanup_flags_cannot_be_combined_with_browser_actions():
    for args in (
        ["--dry-run"],
        ["--cleanup-data", "--create-profile"],
        ["--cleanup-data", "--smoke-browser"],
    ):
        with pytest.raises(SystemExit):
            parse_args(args)


@pytest.mark.parametrize(
    "state,code",
    [
        ("CONFIRMED_SUCCESS", 0),
        ("CONFIRMED_NO_EFFECT", 1),
        ("AWAITING_MANUAL_CONFIRMATION", 2),
        ("OUTCOME_UNKNOWN", 3),
    ],
)
async def test_cli_propagates_outcome_exit(tmp_path, monkeypatch, state, code):
    config = tmp_path / "config.yaml"
    config.write_text("browser:\n  launch_persistent_context: false\n")

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(main_module, "PlaywrightClient", Client)
    monkeypatch.setattr(
        main_module,
        "build_runner",
        lambda *_a, **_kw: SimpleNamespace(
            run=AsyncMock(return_value=RunResult(state=state))
        ),
    )
    monkeypatch.setattr(
        main_module,
        "build_run_reporter",
        lambda _: SimpleNamespace(jsonl_path=None, emit_event=AsyncMock()),
    )
    with pytest.raises(SystemExit) as exit:
        await main_module.main([str(config)])
    assert exit.value.code == code
