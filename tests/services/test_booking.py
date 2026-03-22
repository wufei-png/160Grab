from pathlib import Path

import pytest

from grab.services.booking import BookingService, PageBookingStrategy


class FakeBookingPage:
    def __init__(self, booking_html: str, success_html: str):
        self.booking_html = booking_html
        self.success_html = success_html
        self.current_html = booking_html
        self.evaluated: list[dict] = []
        self.submit_attempts = 0

    async def goto(self, url: str):
        self.last_url = url
        self.current_html = self.booking_html

    async def content(self) -> str:
        return self.current_html

    async def click(self, selector: str):
        assert selector == "#submit_booking"
        self.submit_attempts += 1
        if self.submit_attempts == 3:
            self.current_html = self.success_html

    async def evaluate(self, script: str, arg: dict):
        self.evaluated.append(arg)


@pytest.fixture
def booking_page_html():
    return Path("tests/fixtures/booking_page.html").read_text()


@pytest.fixture
def booking_submit_success_html():
    return Path("tests/fixtures/booking_submit_success.html").read_text()


@pytest.fixture
def page_booking_strategy(booking_page_html, booking_submit_success_html):
    page = FakeBookingPage(booking_page_html, booking_submit_success_html)
    strategy = PageBookingStrategy(page=page)
    strategy.prepare_target(unit_id="u1", dept_id="d1", member_id="member-1")
    return strategy


@pytest.fixture
def booking_service(page_booking_strategy):
    return BookingService(page_strategy=page_booking_strategy)


def test_page_booking_strategy_builds_form_from_booking_page(
    page_booking_strategy, booking_page_html
):
    form = page_booking_strategy.parse_booking_form(
        booking_page_html,
        member_id="member-1",
    )

    assert form.member_id == "member-1"
    assert form.schedule_id == "sch-1001"


@pytest.mark.asyncio
async def test_page_booking_strategy_retries_same_slot_three_times(
    page_booking_strategy,
):
    result = await page_booking_strategy.submit_with_retry(
        slot_id="sch-1001",
        max_attempts=3,
    )

    assert result.success is True
    assert result.attempts == 3


def test_booking_service_exposes_page_strategy_only_by_default(booking_service):
    assert booking_service.strategy_name == "page"


@pytest.mark.asyncio
async def test_open_booking_form_prefills_member_id(page_booking_strategy):
    form = await page_booking_strategy.open_booking_form("sch-1001")

    assert form.member_id == "member-1"
    assert page_booking_strategy.page.evaluated[0]["memberId"] == "member-1"


@pytest.mark.asyncio
async def test_open_booking_form_uses_91160_booking_url_shape(page_booking_strategy):
    await page_booking_strategy.open_booking_form("sch-1001")

    assert (
        page_booking_strategy.page.last_url
        == "https://www.91160.com/guahao/ystep1/uid-u1/depid-d1/schid-sch-1001.html"
    )


def test_prepare_target_must_run_before_opening_form(booking_page_html, booking_submit_success_html):
    strategy = PageBookingStrategy(
        page=FakeBookingPage(booking_page_html, booking_submit_success_html)
    )

    with pytest.raises(RuntimeError):
        strategy.build_booking_url("sch-1001")
