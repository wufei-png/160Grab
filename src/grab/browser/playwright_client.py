import asyncio
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

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

from grab.observability.privacy import safe_data
from grab.observability.safe_logging import logger
from grab.utils.private_files import (
    absolute_path,
    ensure_private_directory,
    private_directory,
)


class PlaywrightClient:
    def __init__(
        self,
        headless: bool = True,
        debug_dir: str | Path | None = None,
        include_sensitive_debug: bool = False,
        stealth_enabled: bool = True,
        persistent_context_enabled: bool = False,
        user_data_dir: str | Path | None = None,
    ):
        self.headless = headless
        self.debug_dir = absolute_path(debug_dir) if debug_dir is not None else None
        env_debug = os.getenv("GRAB_DEBUG_DIR")
        self.sensitive_debug_enabled = bool(
            include_sensitive_debug
            and env_debug
            and self.debug_dir == absolute_path(env_debug)
        )
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
            self.user_data_dir = ensure_private_directory(self.user_data_dir)
            try:
                self.context = await self._private_launch(
                    self.playwright.chromium.launch_persistent_context,
                    user_data_dir=str(self.user_data_dir),
                    headless=self.headless,
                )
            except PlaywrightError as exc:
                self._raise_persistent_launch_error(exc)
            logger.info("Persistent browser context launched.")
            self.browser = getattr(self.context, "browser", None)
        else:
            self.browser = await self._private_launch(
                self.playwright.chromium.launch, headless=self.headless
            )
            logger.info("Browser launched")
            self.context = await self.browser.new_context()

        if self.context is None:
            raise RuntimeError("Playwright browser context is not initialized")

        self.context.on("page", self._handle_new_page)
        self.page = await self._select_or_create_page()
        logger.info("Browser page ready.")

    async def _private_launch(self, launch, **kwargs):
        # Chromium's own newly created profile files inherit this umask.
        previous = os.umask(0o077)
        try:
            return await launch(**kwargs)
        finally:
            os.umask(previous)

    async def goto(self, url: str) -> None:
        if self.page is None:
            raise RuntimeError("Call launch() first")
        await self.page.goto(url)
        logger.info("Diagnostic event.")

    async def screenshot(self, path: str) -> None:
        if self.page is None:
            raise RuntimeError("Call launch() first")
        if (
            not self.sensitive_debug_enabled
            or self.debug_dir != absolute_path(path).parent
        ):
            raise RuntimeError("Sensitive screenshot requires the local debug opt-in")
        if not re.fullmatch(
            r"160grab-debug-v1-\d{8}-\d{6}-\d{6}-[a-f0-9]{32}\.png",
            absolute_path(path).name,
        ):
            raise ValueError(
                "Screenshot filename must use the application debug schema"
            )
        data = await self.page.screenshot()
        with private_directory(self.debug_dir) as directory:
            directory.create(absolute_path(path).name, data)

    async def run_in_page(self, script: str, arg: dict | None = None):
        if self.page is None:
            raise RuntimeError("Call launch() first")
        return await self.page.evaluate(script, arg)

    async def capture_snapshot(self, label: str) -> Path | None:
        if self.page is None:
            raise RuntimeError("Call launch() first")
        if self.debug_dir is None:
            logger.debug("Skipping diagnostic snapshot.")
            return None

        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
        stem = f"160grab-debug-v1-{stamp}-{uuid4().hex}"
        metadata = await self.collect_debug_state()
        metadata["schema"] = 1
        metadata["captured_at"] = datetime.now(UTC).isoformat()
        metadata["sensitive_debug"] = self.sensitive_debug_enabled
        with private_directory(self.debug_dir) as directory:
            if self.sensitive_debug_enabled:
                try:
                    directory.create(
                        stem + ".html", (await self.page.content()).encode("utf-8")
                    )
                    metadata["html_saved"] = True
                except Exception:
                    metadata["html_saved"] = False
                try:
                    image = await self.page.screenshot(full_page=True)
                    directory.create(stem + ".png", image)
                    metadata["screenshot_saved"] = True
                except Exception:
                    metadata["screenshot_saved"] = False
            metadata_path = directory.create(
                stem + ".json", json.dumps(metadata, ensure_ascii=False).encode("utf-8")
            )
        logger.info("Diagnostic snapshot saved locally.")
        return metadata_path

    async def collect_debug_state(self) -> dict[str, Any]:
        if self.page is None:
            raise RuntimeError("Call launch() first")
        metadata = {
            "events": [
                self._safe_page_event(event) for event in self._page_events[-50:]
            ],
            "cookies": [],
            "login_form": {},
        }
        if self.context is not None:
            try:
                cookies = await self.context.cookies()
                metadata["cookie_count"] = len(cookies)
                metadata["cookies"] = [
                    {
                        "http_only": cookie.get("httpOnly") is True,
                        "secure": cookie.get("secure") is True,
                        "session": cookie.get("expires") == -1,
                    }
                    for cookie in cookies
                ]
            except Exception:
                metadata["cookies_available"] = False
        try:
            form = await self.page.evaluate("""() => ({
                ticket_present: Boolean(document.querySelector('#ticket')?.value),
                randstr_present: Boolean(document.querySelector('#randstr')?.value),
                captcha_iframe_count: document.querySelectorAll('iframe[src*="captcha"]').length
            })""")
            metadata["login_form"] = safe_data(form)
        except Exception:
            metadata["login_form_available"] = False
        return metadata

    @staticmethod
    def _safe_page_event(event: dict[str, Any]) -> dict[str, Any]:
        kinds = {"console", "pageerror", "response", "requestfailed"}
        kind = event.get("kind")
        return {
            "kind": kind if isinstance(kind, str) and kind in kinds else "unknown",
            **safe_data(event),
        }

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
                "location": self._serialize_location(
                    getattr(message, "location", None)
                ),
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
        self._page_events.append(self._safe_page_event(event))
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
                    "Skipping page preparation because target closed before stealth finished."
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
            logger.warning("New page preparation failed.")

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
