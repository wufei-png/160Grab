from playwright.async_api import async_playwright

from grab.browser.playwright_client import PlaywrightClient
from tests.contracts.booking.scenarios import USERSCRIPT
from tests.observability.test_privacy import POISON, assert_clean


async def test_real_browser_outputs_exclude_synthetic_dom_script_cookie_and_url(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GRAB_DEBUG_DIR", str(tmp_path))
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context(service_workers="block")
        try:
            page = await context.new_page()

            async def serve(route):
                await route.fulfill(
                    content_type="text/html",
                    body=(
                        "<html><title>"
                        + POISON
                        + "</title><script>console.log("
                        + repr(POISON)
                        + ");</script><body>"
                        + POISON
                        + '<input id="ticket" value="'
                        + POISON
                        + '"></body></html>'
                    ),
                )

            await context.route("**/*", serve)
            await context.add_cookies(
                [
                    {
                        "name": "synthetic_session",
                        "value": POISON,
                        "url": "https://synthetic.invalid/",
                    }
                ]
            )
            client = PlaywrightClient(debug_dir=tmp_path)
            client.page, client.context = page, context
            client._install_page_listeners(page)
            await page.goto("https://synthetic.invalid/?token=" + POISON)
            path = await client.capture_snapshot(POISON)
            assert_clean(path.read_text(encoding="utf-8"))
            assert_clean(await client.collect_debug_state())
            assert [p.suffix for p in tmp_path.iterdir()] == [".json"]
            await page.evaluate(
                "globalThis.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__ = true"
            )
            await page.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
            result = await page.evaluate(
                """(poison) => {
                const output = [];
                console.log = console.warn = console.error = (...args) => output.push(args);
                const hooks = globalThis.__GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
                hooks.appendLog('error', poison, {value: poison, url: location.href, ready: false});
                return {output, state: sessionStorage.getItem(hooks.STATE_KEY)};
            }""",
                POISON,
            )
            assert_clean(result)
        finally:
            await context.close()
            await browser.close()
