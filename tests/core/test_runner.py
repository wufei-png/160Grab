from datetime import datetime, timedelta

import pytest

from grab.core.runner import GrabRunner
from grab.core.scheduler import Scheduler
from grab.models.schemas import BookingResult, DoctorPageTarget, GrabConfig, Slot


class FrozenClock:
    def __init__(self):
        self.current = datetime(2026, 3, 24, 8, 0, 0)
        self.sleep_calls: list[int] = []

    def now(self) -> datetime:
        return self.current

    async def sleep(self, seconds: int):
        self.sleep_calls.append(seconds)
        self.current += timedelta(seconds=seconds)


class FakeAuthService:
    def __init__(self):
        self.calls = 0

    async def ensure_login(self):
        self.calls += 1


class FakeSessionService:
    def __init__(self):
        self.target = DoctorPageTarget(
            unit_id="21",
            dept_id="0",
            doctor_id="14765",
            source_url="https://www.91160.com/doctors/index/unit_id-21/dep_id-0/docid-14765.html",
        )
        self.capture_calls = 0
        self.member_calls = 0

    async def capture_target_from_current_page(self):
        self.capture_calls += 1
        return self.target

    async def resolve_member_id(self):
        self.member_calls += 1
        return "m1"


class FakeScheduleService:
    def __init__(self, slots: list[Slot]):
        self.slots = slots
        self.poll_calls = 0
        self.target = None

    def set_target(self, target):
        self.target = target

    async def poll(self):
        self.poll_calls += 1
        yield self.slots


class FakeBookingService:
    def __init__(self, result: BookingResult):
        self.result = result
        self.calls = 0
        self.prepared = None

    def prepare(self, target, member_id: str):
        self.prepared = (target, member_id)

    async def try_book_first_available(self, slots: list[Slot]) -> BookingResult:
        self.calls += 1
        return self.result


@pytest.fixture
def frozen_clock():
    return FrozenClock()


@pytest.fixture
def runner(frozen_clock):
    config = GrabConfig(
        enable_appoint=True,
        appoint_time=frozen_clock.now() + timedelta(seconds=15),
    )
    scheduler = Scheduler(config, now=frozen_clock.now, sleep=frozen_clock.sleep)
    schedule_service = FakeScheduleService(
        [Slot(schedule_id="sch-1001", doctor_id="14765", time_range="08:00-08:30")]
    )
    booking_service = FakeBookingService(
        BookingResult(success=True, attempts=1, slot_id="sch-1001")
    )
    session_service = FakeSessionService()
    return GrabRunner(
        auth_service=FakeAuthService(),
        session_service=session_service,
        scheduler=scheduler,
        schedule_service=schedule_service,
        booking_service=booking_service,
    )


@pytest.mark.asyncio
async def test_runner_prepares_target_and_member_before_polling(runner):
    await runner.run()

    assert runner.session_service.capture_calls == 1
    assert runner.session_service.member_calls == 1
    assert runner.schedule_service.target.doctor_id == "14765"
    assert runner.booking_service.prepared[1] == "m1"


@pytest.mark.asyncio
async def test_runner_waits_until_appoint_time_before_polling(runner, frozen_clock):
    await runner.run()

    assert frozen_clock.sleep_calls == [5, 5, 5]


@pytest.mark.asyncio
async def test_runner_stops_after_successful_booking(runner):
    result = await runner.run()

    assert result.success is True
    assert result.booked_slot_id == "sch-1001"
