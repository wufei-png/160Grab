import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from playwright_stealth.stealth import Stealth


class PlaywrightClient:
    def __init__(
        self,
        headless: bool = True,
        debug_dir: str | Path | None = None,
        stealth_enabled: bool = True,
    ):
        self.headless = headless
        self.debug_dir = Path(debug_dir) if debug_dir is not None else None
        self.stealth_enabled = stealth_enabled
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.playwright = None
        self._page_events: list[dict[str, Any]] = []

    async def launch(self) -> None:
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        logger.info("Browser launched")
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        if self.stealth_enabled:
            # Apply stealth patches to avoid anti-bot detection.
            stealth = Stealth()
            await stealth.apply_stealth_async(self.page)
        self._install_page_listeners()
        logger.info("Browser page ready (stealth={})", self.stealth_enabled)

    async def goto(self, url: str) -> None:
        if self.page is None:
            raise RuntimeError("Call launch() first")
        await self.page.goto(url)
        logger.info(f"Navigated to {url}")

    async def screenshot(self, path: str) -> None:
        if self.page is None:
            raise RuntimeError("Call launch() first")
        await self.page.screenshot(path=path)
        logger.info(f"Screenshot saved to {path}")

    async def run_in_page(self, script: str, arg: dict | None = None):
        if self.page is None:
            raise RuntimeError("Call launch() first")
        return await self.page.evaluate(script, arg)

    async def capture_snapshot(self, label: str) -> Path | None:
        if self.page is None:
            raise RuntimeError("Call launch() first")
        if self.debug_dir is None:
            logger.debug("Skipping page snapshot for {} because debug_dir is unset", label)
            return None

        self.debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        slug = self._slugify(label)
        base = self.debug_dir / f"{stamp}-{slug}"

        metadata = await self.collect_debug_state()
        metadata["label"] = label
        metadata["captured_at"] = datetime.now(UTC).isoformat()
        metadata["html_path"] = None
        metadata["screenshot_path"] = None

        html_path = base.with_suffix(".html")
        screenshot_path = base.with_suffix(".png")
        metadata_path = base.with_suffix(".json")

        try:
            html_path.write_text(await self.page.content(), encoding="utf-8")
            metadata["html_path"] = str(html_path)
        except Exception as exc:  # pragma: no cover - diagnostic best effort
            metadata["html_error"] = str(exc)

        try:
            await self.page.screenshot(path=str(screenshot_path), full_page=True)
            metadata["screenshot_path"] = str(screenshot_path)
        except Exception as exc:  # pragma: no cover - diagnostic best effort
            metadata["screenshot_error"] = str(exc)

        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Saved page snapshot to {}", metadata_path)
        return metadata_path

    async def collect_debug_state(self) -> dict[str, Any]:
        if self.page is None:
            raise RuntimeError("Call launch() first")

        metadata = {
            "url": self.page.url,
            "title": None,
            "events": self._page_events[-50:],
            "cookies": [],
            "login_form": None,
        }

        try:
            metadata["title"] = await self.page.title()
        except Exception as exc:  # pragma: no cover - diagnostic best effort
            metadata["title"] = f"<title unavailable: {exc}>"

        if self.context is not None:
            try:
                cookies = await self.context.cookies()
                metadata["cookies"] = [
                    {
                        "name": cookie.get("name"),
                        "domain": cookie.get("domain"),
                        "path": cookie.get("path"),
                        "expires": cookie.get("expires"),
                    }
                    for cookie in cookies
                ]
            except Exception as exc:  # pragma: no cover - diagnostic best effort
                metadata["cookies_error"] = str(exc)

        try:
            metadata["login_form"] = await self.page.evaluate(
                """() => {
                    const readText = (selector) => {
                        const node = document.querySelector(selector);
                        return node ? (node.textContent || '').trim() : '';
                    };
                    const readValue = (selector) => {
                        const node = document.querySelector(selector);
                        return node ? (node.value || '') : '';
                    };
                    const collectTexts = (selectors) => {
                        const items = [];
                        selectors.forEach((selector) => {
                            document.querySelectorAll(selector).forEach((node) => {
                                const text = (node.textContent || '').trim();
                                if (text && !items.includes(text)) {
                                    items.push(text);
                                }
                            });
                        });
                        return items;
                    };
                    return {
                        username_length: readValue('#_username').length,
                        password_length: readValue('#_loginPass').length,
                        username_error: readText('#_username_msg'),
                        password_error: readText('#_loginPass_msg'),
                        ticket_present: readValue('#ticket').length > 0,
                        randstr_present: readValue('#randstr').length > 0,
                        target_value: readValue('input[name="target"]'),
                        error_num: readValue('#error_num'),
                        captcha_iframe_count: document.querySelectorAll('iframe[src*="captcha"]').length,
                        visible_messages: collectTexts([
                            '#_username_msg',
                            '#_loginPass_msg',
                            '.wrong',
                            '.warning',
                            '.import',
                            '.fine',
                            '.tips_in_word',
                            '.layui-layer-content',
                            '.swal2-html-container',
                        ]),
                    };
                }"""
            )
        except Exception as exc:  # pragma: no cover - diagnostic best effort
            metadata["login_form_error"] = str(exc)

        return metadata

    async def close(self) -> None:
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("Browser closed")

    async def __aenter__(self) -> "PlaywrightClient":
        await self.launch()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    def _install_page_listeners(self) -> None:
        if self.page is None:
            return

        self.page.on("console", self._record_console_message)
        self.page.on("pageerror", self._record_page_error)
        self.page.on("response", self._record_response)
        self.page.on("requestfailed", self._record_request_failed)

    def _record_console_message(self, message) -> None:
        self._append_event(
            {
                "kind": "console",
                "type": getattr(message, "type", ""),
                "text": getattr(message, "text", ""),
                "location": self._serialize_location(getattr(message, "location", None)),
            }
        )

    def _record_page_error(self, error) -> None:
        self._append_event({"kind": "pageerror", "text": str(error)})

    def _record_response(self, response) -> None:
        request = getattr(response, "request", None)
        self._append_event(
            {
                "kind": "response",
                "method": getattr(request, "method", ""),
                "url": getattr(response, "url", ""),
                "status": getattr(response, "status", None),
                "ok": getattr(response, "ok", None),
            }
        )

    def _record_request_failed(self, request) -> None:
        failure = getattr(request, "failure", None)
        self._append_event(
            {
                "kind": "requestfailed",
                "method": getattr(request, "method", ""),
                "url": getattr(request, "url", ""),
                "failure": getattr(failure, "error_text", None) or str(failure or ""),
            }
        )

    def _append_event(self, event: dict[str, Any]) -> None:
        event["timestamp"] = datetime.now(UTC).isoformat()
        self._page_events.append(event)
        if len(self._page_events) > 200:
            del self._page_events[:-200]

    @staticmethod
    def _serialize_location(location: Any) -> dict[str, Any] | None:
        if location is None:
            return None
        return {
            "url": getattr(location, "url", ""),
            "line": getattr(location, "lineNumber", None),
            "column": getattr(location, "columnNumber", None),
        }

    @staticmethod
    def _slugify(label: str) -> str:
        slug = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in label)
        slug = slug.strip("-")
        return slug or "snapshot"
