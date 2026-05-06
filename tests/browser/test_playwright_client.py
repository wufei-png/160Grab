import asyncio
from pathlib import Path

import pytest

import grab.browser.playwright_client as playwright_client_module
from grab.browser.playwright_client import PlaywrightClient


class FakeSnapshotPage:
    def __init__(self):
        self.url = "https://user.91160.com/login.html"
        self.screenshot_calls: list[tuple[str, bool]] = []
        self.listeners: dict[str, object] = {}
        self.closed = False

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

    def is_closed(self) -> bool:
        return self.closed


class FakeLaunchPage(FakeSnapshotPage):
    def __init__(self, url: str = "about:blank"):
        super().__init__()
        self.url = url


class FakeContext:
    def __init__(self, pages=None):
        self.pages = pages or []
        self.listeners: dict[str, object] = {}
        self.new_page_calls = 0
        self.close_calls = 0

    async def new_page(self):
        page = FakeLaunchPage(url=f"about:blank#{self.new_page_calls}")
        self.new_page_calls += 1
        self.pages.append(page)
        handler = self.listeners.get("page")
        if handler is not None:
            handler(page)
        return page

    async def close(self):
        self.close_calls += 1

    async def cookies(self):
        return []

    def on(self, event: str, handler):
        self.listeners[event] = handler

    def emit_new_page(self, page):
        self.pages.append(page)
        handler = self.listeners.get("page")
        if handler is not None:
            handler(page)


class FakeBrowser:
    def __init__(self, context):
        self.context = context
        self.close_calls = 0

    async def new_context(self):
        return self.context

    async def close(self):
        self.close_calls += 1


class FakeChromium:
    def __init__(self, *, browser=None, persistent_context=None):
        self.browser = browser
        self.persistent_context = persistent_context
        self.launch_calls: list[bool] = []
        self.launch_persistent_calls: list[tuple[str, bool]] = []

    async def launch(self, headless: bool = True):
        self.launch_calls.append(headless)
        return self.browser

    async def launch_persistent_context(self, user_data_dir: str, headless: bool = True):
        self.launch_persistent_calls.append((user_data_dir, headless))
        return self.persistent_context


class FakePlaywright:
    def __init__(self, chromium):
        self.chromium = chromium
        self.stop_calls = 0

    async def stop(self):
        self.stop_calls += 1


class FakePlaywrightManager:
    def __init__(self, playwright):
        self.playwright = playwright

    async def start(self):
        return self.playwright


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


@pytest.mark.asyncio
async def test_launch_transient_context_creates_new_page_and_closes_browser(monkeypatch):
    context = FakeContext()
    browser = FakeBrowser(context)
    chromium = FakeChromium(browser=browser)
    playwright = FakePlaywright(chromium)
    stealth_pages: list[FakeLaunchPage] = []

    class FakeStealth:
        async def apply_stealth_async(self, page):
            stealth_pages.append(page)

    monkeypatch.setattr(
        playwright_client_module,
        "async_playwright",
        lambda: FakePlaywrightManager(playwright),
    )
    monkeypatch.setattr(playwright_client_module, "Stealth", FakeStealth)

    client = PlaywrightClient(headless=False, stealth_enabled=True)
    await client.launch()

    assert chromium.launch_calls == [False]
    assert context.new_page_calls == 1
    assert client.page is context.pages[0]
    assert stealth_pages == [client.page]
    assert set(client.page.listeners) == {
        "console",
        "pageerror",
        "response",
        "requestfailed",
    }

    popup = FakeLaunchPage(url="https://example.com")
    context.emit_new_page(popup)
    await asyncio.sleep(0)

    assert popup in stealth_pages
    assert set(popup.listeners) == {
        "console",
        "pageerror",
        "response",
        "requestfailed",
    }

    await client.close()

    assert browser.close_calls == 1
    assert context.close_calls == 0
    assert playwright.stop_calls == 1


@pytest.mark.asyncio
async def test_launch_persistent_context_reuses_existing_page_and_closes_context(
    monkeypatch, tmp_path
):
    existing_page = FakeLaunchPage(url="https://user.91160.com/member.html")
    context = FakeContext(pages=[existing_page])
    chromium = FakeChromium(persistent_context=context)
    playwright = FakePlaywright(chromium)
    stealth_pages: list[FakeLaunchPage] = []

    class FakeStealth:
        async def apply_stealth_async(self, page):
            stealth_pages.append(page)

    monkeypatch.setattr(
        playwright_client_module,
        "async_playwright",
        lambda: FakePlaywrightManager(playwright),
    )
    monkeypatch.setattr(playwright_client_module, "Stealth", FakeStealth)

    client = PlaywrightClient(
        headless=True,
        stealth_enabled=True,
        persistent_context_enabled=True,
        user_data_dir=tmp_path / "profile_1",
    )
    await client.launch()

    assert chromium.launch_persistent_calls == [(str(tmp_path / "profile_1"), True)]
    assert client.page is existing_page
    assert context.new_page_calls == 0
    assert stealth_pages == [existing_page]

    await client.close()

    assert context.close_calls == 1
    assert playwright.stop_calls == 1


@pytest.mark.asyncio
async def test_prepare_page_ignores_closed_target_errors(monkeypatch):
    class ClosedTargetStealth:
        async def apply_stealth_async(self, page):
            page.closed = True
            raise playwright_client_module.PlaywrightError(
                "Target page, context or browser has been closed"
            )

    monkeypatch.setattr(playwright_client_module, "Stealth", ClosedTargetStealth)

    client = PlaywrightClient(stealth_enabled=True)
    page = FakeLaunchPage(url="https://example.com")

    await client._prepare_page(page)

    assert id(page) in client._prepared_pages
    assert page.listeners == {}
