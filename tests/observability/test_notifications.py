import pytest

from grab.observability.notifications import (
    HttpWebhookNotifier,
    MacOSDesktopNotifier,
    NullDesktopNotifier,
    WindowsDesktopNotifier,
    build_desktop_notifier,
)


def test_build_desktop_notifier_chooses_windows_provider():
    notifier = build_desktop_notifier(enabled=True, system_name="Windows")

    assert isinstance(notifier, WindowsDesktopNotifier)


def test_build_desktop_notifier_chooses_macos_provider():
    notifier = build_desktop_notifier(enabled=True, system_name="Darwin")

    assert isinstance(notifier, MacOSDesktopNotifier)


def test_build_desktop_notifier_falls_back_to_null_provider():
    notifier = build_desktop_notifier(enabled=True, system_name="Linux")

    assert isinstance(notifier, NullDesktopNotifier)


@pytest.mark.asyncio
async def test_windows_desktop_notifier_invokes_powershell():
    calls: list[list[str]] = []
    notifier = WindowsDesktopNotifier(run_command=lambda command: calls.append(command))

    await notifier.notify(title="160Grab", message="挂号成功", subtitle="测试")

    assert calls
    assert calls[0][0] == "powershell.exe"
    assert calls[0][1:3] == ["-NoProfile", "-Command"]
    assert "160Grab" in calls[0][3]
    assert "挂号成功" in calls[0][3]


@pytest.mark.asyncio
async def test_macos_desktop_notifier_invokes_osascript():
    calls: list[list[str]] = []
    notifier = MacOSDesktopNotifier(run_command=lambda command: calls.append(command))

    await notifier.notify(title="160Grab", message="挂号成功", subtitle="测试")

    assert calls
    assert calls[0][:2] == ["/usr/bin/osascript", "-e"]
    assert "display notification" in calls[0][2]
    assert "160Grab" in calls[0][2]
    assert "挂号成功" in calls[0][2]


@pytest.mark.asyncio
async def test_webhook_notifier_posts_json_payload():
    calls: list[tuple[str, dict, int, dict[str, str]]] = []

    async def fake_post_json(url, body, timeout_seconds, headers):
        calls.append((url, body, timeout_seconds, headers))

    notifier = HttpWebhookNotifier(
        url="https://example.com/hook",
        timeout_seconds=8,
        headers={"X-Test": "1"},
        post_json=fake_post_json,
    )

    await notifier.notify(
        title="160Grab 挂号成功",
        message="Booking succeeded",
        severity="info",
        payload={"event": "booking_succeeded", "run_id": "run-1"},
    )

    assert calls == [
        (
            "https://example.com/hook",
            {
                "event": "booking_succeeded",
                "run_id": "run-1",
                "title": "160Grab 挂号成功",
                "message": "Booking succeeded",
                "severity": "info",
            },
            8,
            {"X-Test": "1"},
        )
    ]
