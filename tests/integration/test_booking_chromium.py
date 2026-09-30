import pytest

from grab.models.schemas import GrabConfig
from grab.services.booking import PageBookingStrategy
from tests.contracts.booking.scenarios import SCENARIOS, USERSCRIPT, read_html


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda scenario: scenario["id"])
async def test_booking_parsers_share_real_chromium_dom(chromium_page, scenario):
    page = chromium_page
    fixture_url = "https://synthetic.invalid/booking.html"
    unexpected_requests = []

    async def serve_fixture(route):
        if route.request.url == fixture_url and route.request.method == "GET":
            await route.fulfill(content_type="text/html", body=read_html(scenario))
        else:
            unexpected_requests.append(route.request.url)
            await route.abort()

    await page.context.route("**/*", serve_fixture)
    await page.goto(fixture_url)
    await page.evaluate(
        """() => {
            globalThis.__GRAB160_DOCTOR_POLLER_DISABLE_AUTO_START__ = true;
            globalThis.__syntheticClicks = 0;
            globalThis.__syntheticSubmits = 0;
            const nativeClick = HTMLElement.prototype.click;
            HTMLElement.prototype.click = function (...args) {
                globalThis.__syntheticClicks += 1;
                return nativeClick.apply(this, args);
            };
            document.addEventListener('submit', (event) => {
                globalThis.__syntheticSubmits += 1;
                event.preventDefault();
            }, true);
        }"""
    )
    await page.evaluate(USERSCRIPT.read_text(encoding="utf-8"))

    strategy = PageBookingStrategy(
        page=page, config=GrabConfig(hours=scenario["filters"]["hours"])
    )
    parsed = strategy.parse_booking_form(
        await page.content(), member_id=scenario["selection"]["member_id"]
    )
    expected = scenario["expected"]["parser"]
    assert parsed.model_dump(include=set(expected)) == expected
    js_result = await page.evaluate(
        """(hours) => {
            const result = globalThis.__GRAB160_DOCTOR_POLLER_TEST_HOOKS__
                .parseBookingFormState({hours}, '');
            return {
                schedule_id: result.scheduleId,
                appointment_value: result.appointmentValue,
                appointment_label: result.appointmentLabel,
                is_valid: result.isValid,
                invalid_reason: result.invalidReason,
            };
        }""",
        scenario["filters"]["hours"],
    )
    assert js_result == expected
    assert (
        await page.locator('[name="sch_date"]').input_value()
        == scenario["selection"]["date"]
    )
    assert await page.locator('[name="member_id"]').input_value() == parsed.member_id
    assert not await page.locator('[name="member_id"]').is_checked()
    assert (
        await page.locator('[type="submit"]').count()
        == scenario["submit_candidate_count"]
    )
    assert (
        await page.evaluate("globalThis.__syntheticClicks")
        == scenario["expected"]["click_count"]
    )
    assert await page.evaluate("globalThis.__syntheticSubmits") == 0
    assert unexpected_requests == []
