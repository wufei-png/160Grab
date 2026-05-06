from __future__ import annotations

import asyncio
import platform
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from xml.sax.saxutils import escape as xml_escape

import httpx


@dataclass
class NotificationDeliveryResult:
    provider: str
    ok: bool
    error: str | None = None


class NullDesktopNotifier:
    provider_name = "desktop:null"

    async def notify(
        self,
        *,
        title: str,
        message: str,
        subtitle: str | None = None,
    ) -> None:
        del title, message, subtitle


class WindowsDesktopNotifier:
    provider_name = "desktop:windows"

    def __init__(self, run_command: Callable[[list[str]], None] | None = None):
        self._run_command = run_command or self._default_run_command

    async def notify(
        self,
        *,
        title: str,
        message: str,
        subtitle: str | None = None,
    ) -> None:
        script = _build_windows_toast_script(
            title=title,
            message=message,
            subtitle=subtitle,
        )
        await asyncio.to_thread(
            self._run_command,
            ["powershell.exe", "-NoProfile", "-Command", script],
        )

    @staticmethod
    def _default_run_command(command: list[str]) -> None:
        subprocess.run(command, check=True, capture_output=True, text=True)


class MacOSDesktopNotifier:
    provider_name = "desktop:macos"

    def __init__(self, run_command: Callable[[list[str]], None] | None = None):
        self._run_command = run_command or self._default_run_command

    async def notify(
        self,
        *,
        title: str,
        message: str,
        subtitle: str | None = None,
    ) -> None:
        script = _build_macos_notification_script(
            title=title,
            message=message,
            subtitle=subtitle,
        )
        await asyncio.to_thread(
            self._run_command,
            ["/usr/bin/osascript", "-e", script],
        )

    @staticmethod
    def _default_run_command(command: list[str]) -> None:
        subprocess.run(command, check=True, capture_output=True, text=True)


class HttpWebhookNotifier:
    provider_name = "webhook:http"

    def __init__(
        self,
        *,
        url: str,
        timeout_seconds: int,
        headers: dict[str, str] | None = None,
        post_json: Callable[[str, dict[str, Any], int, dict[str, str]], Any] | None = None,
    ):
        self._url = url
        self._timeout_seconds = timeout_seconds
        self._headers = headers or {}
        self._post_json = post_json or self._default_post_json

    async def notify(
        self,
        *,
        title: str,
        message: str,
        severity: str,
        payload: dict[str, Any],
    ) -> None:
        body = {
            **payload,
            "title": title,
            "message": message,
            "severity": severity,
        }
        await self._post_json(
            self._url,
            body,
            self._timeout_seconds,
            dict(self._headers),
        )

    @staticmethod
    async def _default_post_json(
        url: str,
        body: dict[str, Any],
        timeout_seconds: int,
        headers: dict[str, str],
    ) -> None:
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            headers=headers,
            trust_env=False,
        ) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()


def build_desktop_notifier(
    *,
    enabled: bool,
    system_name: str | None = None,
) -> NullDesktopNotifier | WindowsDesktopNotifier | MacOSDesktopNotifier:
    if not enabled:
        return NullDesktopNotifier()

    name = system_name or platform.system()
    if name == "Windows":
        return WindowsDesktopNotifier()
    if name == "Darwin":
        return MacOSDesktopNotifier()
    return NullDesktopNotifier()


class NotificationManager:
    def __init__(
        self,
        *,
        desktop_notifier,
        webhook_notifier: HttpWebhookNotifier | None = None,
    ):
        self.desktop_notifier = desktop_notifier
        self.webhook_notifier = webhook_notifier

    @classmethod
    def from_config(cls, config) -> NotificationManager:
        desktop_notifier = build_desktop_notifier(
            enabled=config.desktop,
        )
        webhook_notifier = None
        if config.webhook.url:
            webhook_notifier = HttpWebhookNotifier(
                url=config.webhook.url,
                timeout_seconds=config.webhook.timeout_seconds,
                headers=config.webhook.headers,
            )
        return cls(
            desktop_notifier=desktop_notifier,
            webhook_notifier=webhook_notifier,
        )

    async def notify(
        self,
        *,
        title: str,
        message: str,
        severity: str,
        payload: dict[str, Any],
        subtitle: str | None = None,
    ) -> list[NotificationDeliveryResult]:
        results: list[NotificationDeliveryResult] = []
        results.extend(
            await self._attempt_desktop_notification(
                title=title,
                message=message,
                subtitle=subtitle,
            )
        )
        results.extend(
            await self._attempt_webhook_notification(
                title=title,
                message=message,
                severity=severity,
                payload=payload,
            )
        )
        return results

    async def _attempt_desktop_notification(
        self,
        *,
        title: str,
        message: str,
        subtitle: str | None = None,
    ) -> list[NotificationDeliveryResult]:
        notifier = self.desktop_notifier
        if isinstance(notifier, NullDesktopNotifier):
            return []

        try:
            await notifier.notify(
                title=title,
                message=message,
                subtitle=subtitle,
            )
        except Exception as exc:
            return [
                NotificationDeliveryResult(
                    provider=getattr(notifier, "provider_name", "desktop:unknown"),
                    ok=False,
                    error=str(exc),
                )
            ]

        return [
            NotificationDeliveryResult(
                provider=getattr(notifier, "provider_name", "desktop:unknown"),
                ok=True,
            )
        ]

    async def _attempt_webhook_notification(
        self,
        *,
        title: str,
        message: str,
        severity: str,
        payload: dict[str, Any],
    ) -> list[NotificationDeliveryResult]:
        notifier = self.webhook_notifier
        if notifier is None:
            return []

        try:
            await notifier.notify(
                title=title,
                message=message,
                severity=severity,
                payload=payload,
            )
        except Exception as exc:
            return [
                NotificationDeliveryResult(
                    provider=notifier.provider_name,
                    ok=False,
                    error=str(exc),
                )
            ]

        return [NotificationDeliveryResult(provider=notifier.provider_name, ok=True)]


def _build_windows_toast_script(
    *,
    title: str,
    message: str,
    subtitle: str | None = None,
) -> str:
    safe_title = xml_escape(title)
    safe_subtitle = xml_escape(subtitle or "")
    safe_message = xml_escape(message)
    text_nodes = [f"<text>{safe_title}</text>"]
    if safe_subtitle:
        text_nodes.append(f"<text>{safe_subtitle}</text>")
    text_nodes.append(f"<text>{safe_message}</text>")
    toast_xml = (
        "<toast><visual><binding template=\"ToastGeneric\">"
        + "".join(text_nodes)
        + "</binding></visual></toast>"
    )
    toast_xml = toast_xml.replace("'", "''")
    return (
        "[Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
        "ContentType = WindowsRuntime] > $null; "
        f"$template = '{toast_xml}'; "
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; "
        "$xml.LoadXml($template); "
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
        "$notifier = [Windows.UI.Notifications.ToastNotificationManager]"
        "::CreateToastNotifier('160Grab'); "
        "$notifier.Show($toast)"
    )


def _build_macos_notification_script(
    *,
    title: str,
    message: str,
    subtitle: str | None = None,
) -> str:
    parts = [
        f'display notification "{_escape_applescript(message)}"',
        f'with title "{_escape_applescript(title)}"',
    ]
    if subtitle:
        parts.append(f'subtitle "{_escape_applescript(subtitle)}"')
    return " ".join(parts)


def _escape_applescript(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
