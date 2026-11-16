import json

import pytest

from tests.contracts.booking.scenarios import USERSCRIPT


@pytest.mark.parametrize(
    "condition", ["fresh", "pending", "corrupt", "settings-invalid"]
)
async def test_diagnostic_panel_is_readonly_private_and_actionable(
    chromium_page, condition
):
    page = chromium_page
    requests = []

    async def serve(route):
        requests.append(route.request.url)
        await route.fulfill(body="<p>Synthetic doctor</p>", content_type="text/html")

    await page.context.route("**/*", serve)
    await page.goto(
        "https://www.91160.com/doctors/index/unit_id-u/dep_id-d/docid-doc.html"
    )
    await page.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
    await page.evaluate(
        """async condition => {
        const h = __GRAB160_DOCTOR_POLLER_TEST_HOOKS__;
        h.writeSettings({settingsVersion:5,member:{memberId:'SYN_PRIVATE_MEMBER'},booking:{clinicCard:'SYN_PRIVATE_CARD'}});
        if (condition === 'pending') await h.withBrowserLeader(() => h.beginAttempt(['SYN_PRIVATE_BOOKING']));
        if (condition === 'corrupt') localStorage.setItem(h.JOURNAL_KEY,'SYN_PRIVATE_POISON');
        if (condition === 'settings-invalid') localStorage.setItem(h.SETTINGS_KEY,'SYN_PRIVATE_POISON');
    }""",
        condition,
    )
    before = await page.evaluate(
        "JSON.stringify({local:{...localStorage},session:{...sessionStorage}})"
    )
    request_count = len(requests)
    report = await page.evaluate(
        "__GRAB160_DOCTOR_POLLER_TEST_HOOKS__.diagnosticSnapshot()"
    )
    await page.get_by_role("button", name="自检", exact=True).click()
    output = await page.locator("[data-diagnostics]").inner_text()
    assert "不发起网络请求" in output
    assert "SYN_PRIVATE" not in output + json.dumps(report)
    assert report["live_checked"] is False
    assert (
        await page.evaluate(
            "JSON.stringify({local:{...localStorage},session:{...sessionStorage}})"
        )
        == before
    )
    assert len(requests) == request_count
    codes = {check["code"] for check in report["checks"]}
    expected = {
        "fresh": "journal_not_initialized",
        "pending": "pending_verification",
        "corrupt": "journal_unavailable",
        "settings-invalid": "settings_unavailable",
    }[condition]
    assert expected in codes
    if condition != "fresh":
        assert report["status"] == "blocked"


@pytest.mark.parametrize(
    "viewport", [{"width": 640, "height": 720}, {"width": 1200, "height": 900}]
)
async def test_panel_recovers_saved_offscreen_position_and_window_resize(
    chromium_page, viewport
):
    page = chromium_page
    await page.set_viewport_size(viewport)
    await page.context.route(
        "**/*",
        lambda route: route.fulfill(
            body="<p>Synthetic doctor</p>", content_type="text/html"
        ),
    )
    await page.goto(
        "https://www.91160.com/doctors/index/unit_id-u/dep_id-d/docid-doc.html"
    )
    await page.evaluate(
        "localStorage.setItem('grab160.doctorPagePoller.panelPosition.v2',JSON.stringify({left:5000,top:4000}))"
    )
    await page.evaluate(USERSCRIPT.read_text(encoding="utf-8"))
    within_viewport = """() => {
        const r = document.querySelector('#grab160-doctor-page-poller-panel').getBoundingClientRect();
        return r.left >= 8 && r.top >= 8 && r.right <= innerWidth - 8 && r.bottom <= innerHeight - 8;
    }"""
    assert await page.evaluate(within_viewport)
    await page.get_by_role("button", name="自检", exact=True).click()
    assert await page.evaluate(within_viewport)
    await page.set_viewport_size({"width": 360, "height": 500})
    await page.wait_for_function(within_viewport, timeout=2000)
    await page.get_by_role("button", name="自检", exact=True).click()
    assert await page.locator("[data-diagnostics]").is_visible()
