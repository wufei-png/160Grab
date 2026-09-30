import os
import sys
from pathlib import Path

import pytest

from grab.browser.playwright_client import PlaywrightClient
from grab.models.schemas import GrabConfig
from grab.utils.profile_manager import create_profile


def installed(channel):
    if channel == "chromium":
        return True
    if sys.platform == "darwin":
        app, executable = (
            ("Google Chrome", "Google Chrome")
            if channel == "chrome"
            else ("Microsoft Edge", "Microsoft Edge")
        )
        return Path(f"/Applications/{app}.app/Contents/MacOS/{executable}").is_file()
    if sys.platform == "win32":
        vendor, executable = (
            ("Google/Chrome", "chrome.exe")
            if channel == "chrome"
            else ("Microsoft/Edge", "msedge.exe")
        )
        return any(
            (Path(os.getenv(root, "")) / vendor / "Application" / executable).is_file()
            for root in ["PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"]
        )
    return Path(
        "/opt/google/chrome/chrome"
        if channel == "chrome"
        else "/opt/microsoft/msedge/msedge"
    ).is_file()


@pytest.mark.parametrize("channel", ["chromium", "chrome", "msedge"])
@pytest.mark.parametrize("persistent", [False, True])
async def test_installed_channel_smoke_with_temporary_profile(
    tmp_path, channel, persistent
):
    if not installed(channel):
        pytest.skip(f"{channel} is not installed; parameter forwarding is unit-tested")
    profile = create_profile(tmp_path / "profiles", channel=channel)
    async with PlaywrightClient(
        channel=channel,
        headless=True,
        stealth_enabled=False,
        persistent_context_enabled=persistent,
        user_data_dir=profile.path,
    ) as client:
        await client.context.route("**/*", lambda route: route.abort())
        await client.goto("about:blank")
        await client.page.set_content(
            '<title>S08 synthetic smoke</title><p id="result">ready</p>'
        )
        assert await client.page.title() == "S08 synthetic smoke"
        assert await client.page.locator("#result").inner_text() == "ready"
        assert client.browser.version


def test_default_config_and_tzdata_work_without_system_zoneinfo(monkeypatch):
    import zoneinfo
    from datetime import UTC, datetime

    zoneinfo.ZoneInfo.clear_cache()
    zoneinfo.reset_tzpath(())
    try:
        config = GrabConfig(appoint_time="2030-01-01T08:00:00")
        assert config.browser.channel == "chromium"
        assert config.appoint_time == datetime(2030, 1, 1, tzinfo=UTC)
    finally:
        zoneinfo.ZoneInfo.clear_cache()
        zoneinfo.reset_tzpath()
