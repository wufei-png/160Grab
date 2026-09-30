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


async def test_interactive_manual_handoff_waits_before_browser_closes(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.yaml"
    config.write_text(
        "browser:\n  launch_persistent_context: false\nbooking:\n  submit_mode: manual_confirm\n"
    )
    events = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            events.append("open")
            return self

        async def __aexit__(self, *args):
            events.append("closed")

    def handoff(message):
        assert events == ["open"]
        assert "人工" in message and "关闭浏览器" in message
        events.append("handoff")
        return ""

    monkeypatch.setattr(main_module, "PlaywrightClient", Client)
    monkeypatch.setattr(main_module, "input", handoff, raising=False)
    monkeypatch.setattr(main_module.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(
        main_module,
        "build_runner",
        lambda *_a, **_kw: SimpleNamespace(
            run=AsyncMock(return_value=RunResult(state="AWAITING_MANUAL_CONFIRMATION"))
        ),
    )
    monkeypatch.setattr(
        main_module,
        "build_run_reporter",
        lambda _: SimpleNamespace(jsonl_path=None, emit_event=AsyncMock()),
    )
    with pytest.raises(SystemExit) as exit:
        await main_module.main([str(config)])
    assert exit.value.code == 2
    assert events == ["open", "handoff", "closed"]


@pytest.mark.parametrize("channel", ["chromium", "chrome", "msedge"])
async def test_create_warmup_uses_configured_channel(tmp_path, monkeypatch, channel):
    from grab.models.schemas import GrabConfig
    from grab.utils.profile_manager import load_profile

    captured = []

    class Client:
        def __init__(self, **kwargs):
            captured.append(kwargs)
            self.page = SimpleNamespace(goto=AsyncMock())
            self.context = SimpleNamespace(wait_for_event=AsyncMock())

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(main_module, "PlaywrightClient", Client)
    config = GrabConfig(
        browser={"channel": channel, "profiles_root_dir": str(tmp_path)}
    )
    await main_module.run_create_profile_flow(
        config=config, requested_profile_name="synthetic", debug_dir=None
    )
    profile = load_profile(tmp_path, "synthetic", channel=channel)
    assert captured[0]["channel"] == channel
    assert captured[0]["user_data_dir"] == profile.path
    assert captured[0]["persistent_context_enabled"] is True


@pytest.mark.parametrize("persistent", [False, True])
@pytest.mark.parametrize("channel", ["chromium", "chrome", "msedge"])
async def test_cli_forwards_configured_channel_to_run(
    tmp_path, monkeypatch, channel, persistent
):
    from grab.utils.profile_manager import load_profile

    config = tmp_path / "config.yaml"
    config.write_text(
        f"browser:\n  channel: {channel}\n  launch_persistent_context: {str(persistent).lower()}\n  profiles_root_dir: {tmp_path}/profiles\n"
    )
    captured = []

    class Client:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(main_module, "PlaywrightClient", Client)
    monkeypatch.setattr(
        main_module,
        "build_runner",
        lambda *_a, **_kw: SimpleNamespace(
            run=AsyncMock(return_value=RunResult(state="CONFIRMED_SUCCESS"))
        ),
    )
    monkeypatch.setattr(
        main_module,
        "build_run_reporter",
        lambda _: SimpleNamespace(jsonl_path=None, emit_event=AsyncMock()),
    )
    with pytest.raises(SystemExit) as caught:
        await main_module.main([str(config)])
    assert caught.value.code == 0
    assert captured[0]["channel"] == channel
    if persistent:
        assert (
            load_profile(tmp_path / "profiles", "profile_1", channel=channel).path
            == captured[0]["user_data_dir"]
        )


async def test_cli_reports_selected_browser_install_failure_safely(monkeypatch, capsys):
    from grab.errors import BrowserLaunchError

    monkeypatch.setattr(
        main_module,
        "_exclusive_main",
        AsyncMock(side_effect=BrowserLaunchError("SYN_SECRET")),
    )
    with pytest.raises(SystemExit) as caught:
        await main_module.main([])
    assert caught.value.code == 1
    output = capsys.readouterr()
    assert "browser.channel" in output.out
    assert "SYN_SECRET" not in output.out + output.err


async def test_invalid_browser_channel_stops_before_any_browser_or_profile(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.yaml"
    config.write_text("browser:\n  channel: chrome-beta\n")
    monkeypatch.setattr(
        main_module,
        "PlaywrightClient",
        lambda **_: pytest.fail("browser must not launch"),
    )
    monkeypatch.setattr(
        main_module,
        "resolve_profile_for_run",
        lambda **_: pytest.fail("profile must not resolve"),
    )
    with pytest.raises(SystemExit) as caught:
        await main_module.main([str(config)])
    assert caught.value.code == 1


@pytest.mark.parametrize("persistent", [False, True])
async def test_normal_cli_browser_failure_keeps_install_guidance(
    tmp_path, monkeypatch, persistent
):
    from grab.errors import BrowserLaunchError

    config = tmp_path / "config.yaml"
    config.write_text(
        f"browser:\n  channel: chrome\n  launch_persistent_context: {str(persistent).lower()}\n  profiles_root_dir: {tmp_path}/profiles\n"
    )
    messages = []
    events = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["channel"] == "chrome"

        async def __aenter__(self):
            raise BrowserLaunchError("SYN_SECRET")

        async def __aexit__(self, *args):
            pass

    async def emit_event(*args, **kwargs):
        events.append(args[0])

    monkeypatch.setattr(main_module, "PlaywrightClient", Client)
    monkeypatch.setattr(
        main_module,
        "build_run_reporter",
        lambda _: SimpleNamespace(
            jsonl_path=None, current_phase="startup", emit_event=emit_event
        ),
    )
    monkeypatch.setattr(main_module, "emit_console_message", messages.append)
    with pytest.raises(SystemExit) as caught:
        await main_module.main([str(config)])
    assert caught.value.code == 1
    assert "browser.channel" in "\n".join(messages)
    assert "SYN_SECRET" not in "\n".join(messages)
    assert "run_finished" in events
