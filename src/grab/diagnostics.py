"""Read-only startup checks. Never launch a browser or initialize user storage."""

import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import async_playwright

from grab.transactions.store import DEFAULT_STATE_DIR, UNRESOLVED, AttemptStore
from grab.utils.private_files import private_directory
from grab.utils.profile_manager import list_profiles


async def browser_available(channel):
    if channel == "chromium":
        async with async_playwright() as playwright:
            return Path(playwright.chromium.executable_path).is_file()
    if sys.platform == "darwin":
        name = "Google Chrome" if channel == "chrome" else "Microsoft Edge"
        return Path(f"/Applications/{name}.app/Contents/MacOS/{name}").is_file()
    if os.name == "nt":
        suffix = (
            "Google/Chrome/Application/chrome.exe"
            if channel == "chrome"
            else "Microsoft/Edge/Application/msedge.exe"
        )
        return any(
            (Path(os.environ[key]) / suffix).is_file()
            for key in ("LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")
            if os.environ.get(key)
        )
    names = (
        ("google-chrome", "google-chrome-stable")
        if channel == "chrome"
        else ("microsoft-edge", "microsoft-edge-stable")
    )
    return any(shutil.which(name) for name in names)


def journal_check(root):
    try:
        with private_directory(root, create=False) as directory:
            state = json.loads(directory.read("journal.json"))
        AttemptStore._validate(state)
        count = sum(record["state"] in UNRESOLVED for record in state["attempts"])
        return {
            "id": "journal",
            "status": "blocked" if count else "ok",
            "code": "pending_verification" if count else "journal_valid",
            "pending_count": count,
        }
    except FileNotFoundError:
        return {"id": "journal", "status": "warning", "code": "journal_not_initialized"}
    except (OSError, ValueError, TypeError, KeyError):
        return {"id": "journal", "status": "blocked", "code": "journal_unavailable"}


async def check_environment(config, *, journal_root=DEFAULT_STATE_DIR):
    checks = [{"id": "configuration", "status": "ok", "code": "configuration_valid"}]
    try:
        installed = await browser_available(config.browser.channel)
    except Exception:
        installed = False
    checks.append(
        {
            "id": "browser",
            "status": "ok" if installed else "blocked",
            "code": "browser_installed" if installed else "browser_missing",
        }
    )
    if config.browser.launch_persistent_context:
        try:
            profiles = list_profiles(config.browser.profiles_root_dir)
            if config.browser.profile_name:
                matches = [p for p in profiles if p.name == config.browser.profile_name]
                if not matches:
                    code, status = "profile_unavailable", "blocked"
                elif matches[0].channel != config.browser.channel:
                    code, status = "profile_channel_mismatch", "blocked"
                else:
                    code, status = "profile_valid", "ok"
            else:
                matches = [p for p in profiles if p.channel == config.browser.channel]
                code, status = (
                    ("profile_auto_detectable", "ok")
                    if len(matches) == 1
                    else ("profile_selection_required", "warning")
                    if matches
                    else ("profile_setup_required", "warning")
                )
        except (OSError, ValueError):
            code, status = "profile_unavailable", "blocked"
    else:
        code, status = "transient_browser", "warning"
    checks.append({"id": "profile", "status": status, "code": code})
    checks.append(journal_check(journal_root))
    checks.append(
        {
            "id": "target",
            "status": "ok" if config.doctor_ids else "warning",
            "code": "target_configured"
            if config.doctor_ids
            else "manual_doctor_navigation_required",
        }
    )
    checks.append(
        {
            "id": "member",
            "status": "ok" if config.member_id else "warning",
            "code": "member_configured"
            if config.member_id
            else "member_selection_required",
        }
    )
    if config.enable_appoint and config.appoint_time:
        late = (
            datetime.now(UTC) - config.appoint_time
        ).total_seconds() > config.schedule.late_start_grace_seconds
        checks.append(
            {
                "id": "start_time",
                "status": "warning" if late else "ok",
                "code": "late_start_confirmation_required"
                if late
                else "start_time_valid",
            }
        )
    checks.append(
        {"id": "site_session", "status": "warning", "code": "readonly_canary_required"}
    )
    status = (
        "blocked"
        if any(c["status"] == "blocked" for c in checks)
        else "warning"
        if any(c["status"] == "warning" for c in checks)
        else "ok"
    )
    return {"version": 1, "status": status, "live_checked": False, "checks": checks}


async def diagnostic_report(config_path):
    from grab.utils.config_loader import load_config

    try:
        config = load_config(config_path)
    except Exception:
        return {
            "version": 1,
            "status": "blocked",
            "live_checked": False,
            "checks": [
                {
                    "id": "configuration",
                    "status": "blocked",
                    "code": "configuration_unavailable",
                }
            ],
        }
    return await check_environment(config)
