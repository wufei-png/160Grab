from pathlib import Path

import pytest

from grab.browser.playwright_client import PlaywrightClient


class FakeSnapshotPage:
    def __init__(self):
        self.url = "https://user.91160.com/login.html"
        self.screenshot_calls: list[tuple[str, bool]] = []
        self.listeners: dict[str, object] = {}

    async def title(self) -> str:
        return "健康160登录"

    async def content(self) -> str:
        return "<html><body>login</body></html>"

    async def screenshot(self, path: str, full_page: bool = False):
        self.screenshot_calls.append((path, full_page))
        Path(path).write_bytes(b"png")

    async def evaluate(self, script: str):
        return {
            "username_length": 11,
            "password_length": 8,
            "username_error": "",
            "password_error": "",
            "ticket_present": False,
            "randstr_present": False,
            "target_value": "https://user.91160.com/login.html",
            "error_num": "1",
            "captcha_iframe_count": 1,
            "visible_messages": [],
        }

    def on(self, event: str, handler):
        self.listeners[event] = handler


@pytest.mark.asyncio
async def test_capture_snapshot_writes_html_png_and_metadata(tmp_path):
    client = PlaywrightClient(debug_dir=tmp_path)
    client.page = FakeSnapshotPage()
    client._page_events.append({"kind": "console", "text": "hello"})

    metadata_path = await client.capture_snapshot("login-page")

    assert metadata_path is not None
    assert metadata_path.exists()

    html_path = metadata_path.with_suffix(".html")
    png_path = metadata_path.with_suffix(".png")

    assert html_path.exists()
    assert png_path.exists()
    assert "login" in html_path.read_text(encoding="utf-8")
    assert client.page.screenshot_calls == [(str(png_path), True)]

    metadata = metadata_path.read_text(encoding="utf-8")
    assert "健康160登录" in metadata
    assert "console" in metadata
