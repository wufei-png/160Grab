import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from playwright.async_api import (
    Browser,
    BrowserContext,
    Page,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright_stealth.stealth import Stealth


class PlaywrightClient:
    def __init__(
        self,
        headless: bool = True,
        debug_dir: str | Path | None = None,
        stealth_enabled: bool = True,
        persistent_context_enabled: bool = False,
        user_data_dir: str | Path | None = None,
    ):
        self.headless = headless
        self.debug_dir = Path(debug_dir) if debug_dir is not None else None
        self.stealth_enabled = stealth_enabled
        self.persistent_context_enabled = persistent_context_enabled
        self.user_data_dir = (
            Path(user_data_dir).expanduser() if user_data_dir is not None else None
        )
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.playwright = None
        self._page_events: list[dict[str, Any]] = []
        self._prepared_pages: set[int] = set()
        self._page_prepare_tasks: set[asyncio.Task] = set()

    async def launch(self) -> None:
        self.playwright = await async_playwright().start()
        if self.persistent_context_enabled:
            if self.user_data_dir is None:
                raise RuntimeError(
                    "user_data_dir is required when persistent_context_enabled is True"
                )
            try:
                self.context = await self.playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.user_data_dir),
                    headless=self.headless,
                )
            except PlaywrightError as exc:
                self._raise_persistent_launch_error(exc)
            logger.info("Persistent browser context launched at {}", self.user_data_dir)
            self.browser = getattr(self.context, "browser", None)
        else:
            self.browser = await self.playwright.chromium.launch(headless=self.headless)
            logger.info("Browser launched")
            self.context = await self.browser.new_context()

        if self.context is None:
            raise RuntimeError("Playwright browser context is not initialized")

        self.context.on("page", self._handle_new_page)
        self.page = await self._select_or_create_page()
        logger.info(
            "Browser page ready (stealth={}, persistent_context={})",
            self.stealth_enabled,
            self.persistent_context_enabled,
        )

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
        for task in list(self._page_prepare_tasks):
            await asyncio.gather(task, return_exceptions=True)
        if self.persistent_context_enabled:
            if self.context:
                await self.context.close()
        elif self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        logger.info("Browser closed")

    async def __aenter__(self) -> "PlaywrightClient":
        await self.launch()
        return self

    async def __aexit__(self, *args) -> None:
        await self.close()

    def _install_page_listeners(self, page: Page) -> None:
        if page is None:
            return

        page.on("console", self._record_console_message)
        page.on("pageerror", self._record_page_error)
        page.on("response", self._record_response)
        page.on("requestfailed", self._record_request_failed)

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

    async def _select_or_create_page(self) -> Page:
        if self.context is None:
            raise RuntimeError("Browser context is not initialized")

        if self.context.pages:
            page = self.context.pages[-1]
            await self._prepare_page(page)
            return page

        page = await self.context.new_page()
        await self._prepare_page(page)
        return page

    async def _prepare_page(self, page: Page) -> None:
        page_id = id(page)
        if page_id in self._prepared_pages:
            return

        self._prepared_pages.add(page_id)
        try:
            if self.stealth_enabled:
                stealth = Stealth()
                await stealth.apply_stealth_async(page)
            self._install_page_listeners(page)
        except PlaywrightError as exc:
            if self._is_closed_target_error(page, exc):
                logger.debug(
                    "Skipping page preparation because target closed before stealth finished: {}",
                    exc,
                )
                return
            self._prepared_pages.discard(page_id)
            raise
        except Exception:
            self._prepared_pages.discard(page_id)
            raise

    def _handle_new_page(self, page: Page) -> None:
        task = asyncio.create_task(self._prepare_page(page))
        self._page_prepare_tasks.add(task)
        task.add_done_callback(self._finalize_page_prepare_task)

    def _finalize_page_prepare_task(self, task: asyncio.Task) -> None:
        self._page_prepare_tasks.discard(task)
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.opt(exception=exc).warning("New page preparation failed.")

    def _raise_persistent_launch_error(self, exc: Exception) -> None:
        if self.user_data_dir is None:
            raise RuntimeError("Persistent browser launch failed") from exc

        lock_files = [
            self.user_data_dir / "SingletonLock",
            self.user_data_dir / "SingletonCookie",
            self.user_data_dir / "SingletonSocket",
        ]
        if any(path.exists() for path in lock_files):
            raise RuntimeError(
                f"Profile at {self.user_data_dir} appears to be in use by another browser "
                "instance. Close that browser before retrying."
            ) from exc
        raise RuntimeError(
            f"Failed to launch persistent browser context at {self.user_data_dir}: {exc}"
        ) from exc

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

    @staticmethod
    def _is_closed_target_error(page: Page, exc: PlaywrightError) -> bool:
        try:
            if hasattr(page, "is_closed") and page.is_closed():
                return True
        except Exception:
            pass
        return "has been closed" in str(exc).lower()
