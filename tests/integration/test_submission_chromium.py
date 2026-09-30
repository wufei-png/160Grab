import pytest

from grab.models.schemas import BookingForm, DoctorPageTarget
from grab.services.booking import PageBookingStrategy
from grab.transactions.store import AttemptStore
from tests.integration.test_booking_chromium import chromium_page  # noqa: F401, F811


@pytest.mark.parametrize("effect", ["weak-success", "rate-limit", "lost-response"])
async def test_real_locator_click_is_durable_and_never_repeated(
    chromium_page, tmp_path, effect  # noqa: F811
):
    page = chromium_page
    await page.context.route(
        "**/*", lambda route: route.fulfill(body='<button id="submitbtn">预约</button>')
    )
    await page.goto("https://synthetic.invalid/booking")
    await page.evaluate(
        """(effect) => {
        window.clickCount = 0;
        document.querySelector('#submitbtn').onclick = () => {
            window.clickCount++;
            document.body.innerHTML = effect === 'rate-limit' ? '访问次数过多' : '页面已变化';
        };
    }""",
        effect,
    )
    store = AttemptStore(tmp_path)
    obj = PageBookingStrategy(page, attempt_store=store, authorization=lambda *_: True)
    obj.prepare(
        DoctorPageTarget(unit_id="u", dept_id="d", doctor_id="doc", source_url=""),
        "member",
    )
    result = await obj.submit_open_form(
        BookingForm(member_id="member", schedule_id="slot")
    )
    assert result.state == "OUTCOME_UNKNOWN"
    assert await page.evaluate("window.clickCount") == 1
    await page.reload()
    restarted = PageBookingStrategy(
        page, attempt_store=AttemptStore(tmp_path), authorization=lambda *_: True
    )
    restarted.prepare(obj.target, "member")
    assert (await restarted.submit_with_retry("other")).state == "OUTCOME_UNKNOWN"
    assert store.pending()
