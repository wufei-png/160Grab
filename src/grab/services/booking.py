import re
from typing import Protocol

from loguru import logger

from grab.models.schemas import BookingForm, BookingResult, DoctorPageTarget, GrabConfig
from grab.utils.rate_limit import RateLimitError, raise_if_rate_limited
from grab.utils.runtime import parse_sleep_time


class BookingStrategy(Protocol):
    async def submit_with_retry(
        self,
        slot_id: str,
        max_attempts: int = 3,
    ) -> BookingResult: ...


class PageBookingStrategy:
    def __init__(self, page, config: GrabConfig | None = None, sleep=None):
        self.page = page
        self.config = config
        self._sleep = sleep
        self.member_id: str | None = None
        self.target: DoctorPageTarget | None = None

    def prepare_target(self, unit_id: str, dept_id: str, member_id: str) -> None:
        self.target = DoctorPageTarget(
            unit_id=unit_id,
            dept_id=dept_id,
            doctor_id="",
            source_url="",
        )
        self.member_id = member_id

    def prepare(self, target: DoctorPageTarget, member_id: str) -> None:
        self.target = target
        self.member_id = member_id

    def parse_booking_form(self, booking_page_html: str, member_id: str) -> BookingForm:
        schedule_match = re.search(
            r'name="schedule_id"\s+value="([^"]+)"',
            booking_page_html,
        )
        schedule_id = schedule_match.group(1) if schedule_match else ""
        return BookingForm(
            member_id=member_id,
            schedule_id=schedule_id,
            is_valid=bool(schedule_id),
        )

    async def fetch_booking_form(self, slot_id: str) -> BookingForm:
        await self._sleep_page_action("opening booking form")
        await self.page.goto(self.build_booking_url(slot_id))
        html = await self.page.content()
        raise_if_rate_limited(html, context="booking form page")
        return self.parse_booking_form(html, member_id=self.member_id)

    def build_booking_url(self, slot_id: str) -> str:
        if self.target is None or self.member_id is None:
            raise RuntimeError("Call prepare() before opening booking forms")
        return (
            "https://www.91160.com/guahao/ystep1/"
            f"uid-{self.target.unit_id}/depid-{self.target.dept_id}/schid-{slot_id}.html"
        )

    async def fill_booking_form(self, form: BookingForm) -> None:
        if not hasattr(self.page, "evaluate"):
            return

        await self.page.evaluate(
            """({ memberId }) => {
                const selectors = [
                    'input[name="member_id"]',
                    '#member_id',
                    'input[name="memberId"]',
                    '#memberId',
                ];
                selectors.forEach((selector) => {
                    const input = document.querySelector(selector);
                    if (input) {
                        input.value = memberId;
                    }
                });
            }""",
            {"memberId": form.member_id},
        )

    async def submit_booking_via_page(self, form: BookingForm) -> bool:
        await self._sleep_page_action("submitting booking form")
        await self.page.click("#submit_booking")
        html = await self.page.content()
        raise_if_rate_limited(html, context="booking submit page")
        return "预约成功" in html

    async def open_booking_form(self, slot_id: str) -> BookingForm:
        form = await self.fetch_booking_form(slot_id)
        if form.is_valid:
            await self.fill_booking_form(form)
        return form

    async def submit_open_form(self, form: BookingForm) -> BookingResult:
        success = await self.submit_booking_via_page(form)
        return BookingResult(success=success, attempts=1, slot_id=form.schedule_id)

    async def submit_with_retry(
        self,
        slot_id: str,
        max_attempts: int = 3,
    ) -> BookingResult:
        for attempt in range(1, max_attempts + 1):
            try:
                form = await self.open_booking_form(slot_id)
                if not form.is_valid:
                    if attempt < max_attempts:
                        await self._sleep_retry_gap(attempt)
                    continue
                if await self.submit_booking_via_page(form):
                    return BookingResult(
                        success=True, attempts=attempt, slot_id=slot_id
                    )
            except RateLimitError as exc:
                logger.warning(
                    "Rate limit detected during booking attempt {} for slot {}: {}",
                    attempt,
                    slot_id,
                    exc.message,
                )
                if attempt < max_attempts:
                    await self._sleep_rate_limit_gap()
                continue

            if attempt < max_attempts:
                await self._sleep_retry_gap(attempt)

        return BookingResult(success=False, attempts=max_attempts, slot_id=slot_id)

    async def _sleep_page_action(self, action: str) -> None:
        if self.config is None:
            return
        await self._sleep_for(self.config.page_action_sleep_time, f"before {action}")

    async def _sleep_retry_gap(self, attempt: int) -> None:
        if self.config is None:
            return
        await self._sleep_for(
            self.config.booking_retry_sleep_time,
            f"before booking retry attempt {attempt + 1}",
        )

    async def _sleep_rate_limit_gap(self) -> None:
        if self.config is None:
            return
        await self._sleep_for(
            self.config.rate_limit_sleep_time,
            "for booking rate-limit cooldown",
        )

    async def _sleep_for(self, delay_text: str, reason: str) -> None:
        if self._sleep is None:
            return
        delay_ms = parse_sleep_time(delay_text)
        if delay_ms <= 0:
            return
        logger.debug("Sleeping {} ms {}", delay_ms, reason)
        await self._sleep(delay_ms / 1000)


class BookingService:
    def __init__(self, page_strategy: PageBookingStrategy, strategy_name: str = "page"):
        if strategy_name != "page":
            raise NotImplementedError("Only page booking strategy is implemented")
        self.strategy_name = strategy_name
        self.page_strategy = page_strategy

    def prepare(self, target: DoctorPageTarget, member_id: str) -> None:
        self.page_strategy.prepare(target, member_id)

    async def try_book_first_available(self, slots) -> BookingResult:
        if not slots:
            return BookingResult(success=False, attempts=0, slot_id=None)
        return await self.page_strategy.submit_with_retry(slots[0].schedule_id)

    async def open_booking_form(self, slot) -> BookingForm:
        return await self.page_strategy.open_booking_form(slot.schedule_id)

    async def submit_open_form(self, form: BookingForm) -> BookingResult:
        return await self.page_strategy.submit_open_form(form)
