import pytest
from playwright.async_api import async_playwright


@pytest.fixture
async def chromium_page():
    # A fresh transient context: no local profile, authentication or live network.
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        # Routes still fulfill synthetic responses, but a redirect that bypasses
        # Playwright routing cannot escape to the real network.
        context = await browser.new_context(service_workers="block", offline=True)
        try:
            yield await context.new_page()
        finally:
            await context.close()
            await browser.close()
