import re
from typing import Protocol

from grab.models.schemas import BookingForm, BookingResult, DoctorPageTarget


class BookingStrategy(Protocol):
    async def submit_with_retry(
        self,
        slot_id: str,
        max_attempts: int = 3,
    ) -> BookingResult: ...


class PageBookingStrategy:
    def __init__(self, page):
        self.page = page
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
        await self.page.goto(self.build_booking_url(slot_id))
        return self.parse_booking_form(await self.page.content(), member_id=self.member_id)

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
        await self.page.click("#submit_booking")
        return "预约成功" in await self.page.content()

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
            form = await self.open_booking_form(slot_id)
            if not form.is_valid:
                continue
            if await self.submit_booking_via_page(form):
                return BookingResult(success=True, attempts=attempt, slot_id=slot_id)

        return BookingResult(success=False, attempts=max_attempts, slot_id=slot_id)


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
