from loguru import logger
from playwright.async_api import Browser, BrowserContext, Page, async_playwright


class PlaywrightClient:
    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.playwright = None

    async def launch(self) -> None:
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=self.headless)
        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()
        logger.info("Browser launched")

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
