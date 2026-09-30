from datetime import datetime, timedelta

import pytest

from grab.core.runner import GrabRunner
from grab.core.scheduler import Scheduler
from grab.errors import SessionExpiredError
from grab.models.schemas import (
    BookingResult,
    BookingState,
    DoctorPageTarget,
    GrabConfig,
    Slot,
)


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
    def __init__(
        self,
        target: DoctorPageTarget | None = None,
        resolved_target=None,
        config: GrabConfig | None = None,
    ):
        self.target = target or DoctorPageTarget(
            unit_id="21",
            dept_id="369",
            doctor_id="14765",
            source_url="https://www.91160.com/doctors/index/unit_id-21/dep_id-369/docid-14765.html",
        )
        self.resolved_target = resolved_target or self.target
        self.config = config or GrabConfig()
        self.capture_calls = 0
        self.member_calls = 0
        self.resolution_calls = 0

    async def capture_target_from_current_page(self):
        self.capture_calls += 1
        return self.target

    async def resolve_unit_dept_ids(self, target):
        self.resolution_calls += 1
        return self.resolved_target

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


class RecoveringScheduleService(FakeScheduleService):
    def __init__(self, slots: list[Slot]):
        super().__init__(slots)
        self._first_poll = True

    async def poll(self):
        self.poll_calls += 1
        if self._first_poll:
            self._first_poll = False
            raise SessionExpiredError("Could not resolve _user_key/access_hash")
        yield self.slots


class AlwaysExpiredScheduleService(FakeScheduleService):
    async def poll(self):
        self.poll_calls += 1
        raise SessionExpiredError("Could not resolve _user_key/access_hash")
        yield []


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


class FakeReporter:
    def __init__(self):
        self.phase = "startup"
        self.events: list[dict] = []

    def set_phase(self, phase: str) -> None:
        self.phase = phase

    async def emit_event(self, event: str, **kwargs):
        self.events.append({"event": event, **kwargs, "phase": self.phase})

    def reset_rate_limit_streak(self) -> None:
        return None


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
    session_service = FakeSessionService(config=config)
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
    assert runner.session_service.resolution_calls == 0
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


@pytest.mark.asyncio
async def test_runner_resolves_docid_only_target_before_polling(frozen_clock):
    unresolved_target = DoctorPageTarget(
        unit_id=None,
        dept_id=None,
        doctor_id="14765",
        source_url="https://www.91160.com/doctors/index/docid-14765.html",
        needs_resolution=True,
    )
    resolved_target = DoctorPageTarget(
        unit_id="21",
        dept_id="369",
        doctor_id="14765",
        source_url=unresolved_target.source_url,
        needs_resolution=False,
    )
    config = GrabConfig(
        enable_appoint=True,
        appoint_time=frozen_clock.now() + timedelta(seconds=15),
    )
    runner = GrabRunner(
        auth_service=FakeAuthService(),
        session_service=FakeSessionService(
            target=unresolved_target,
            resolved_target=resolved_target,
            config=config,
        ),
        scheduler=Scheduler(config, now=frozen_clock.now, sleep=frozen_clock.sleep),
        schedule_service=FakeScheduleService(
            [Slot(schedule_id="sch-1001", doctor_id="14765", time_range="08:00-08:30")]
        ),
        booking_service=FakeBookingService(
            BookingResult(success=True, attempts=1, slot_id="sch-1001")
        ),
    )

    await runner.run()

    assert runner.session_service.resolution_calls == 1
    assert runner.schedule_service.target.unit_id == "21"
    assert runner.booking_service.prepared[0].dept_id == "369"


@pytest.mark.asyncio
async def test_runner_fails_when_docid_only_target_stays_unresolved(frozen_clock):
    unresolved_target = DoctorPageTarget(
        unit_id=None,
        dept_id=None,
        doctor_id="14765",
        source_url="https://www.91160.com/doctors/index/docid-14765.html",
        needs_resolution=True,
    )
    config = GrabConfig(
        enable_appoint=True,
        appoint_time=frozen_clock.now() + timedelta(seconds=15),
    )
    runner = GrabRunner(
        auth_service=FakeAuthService(),
        session_service=FakeSessionService(target=unresolved_target, config=config),
        scheduler=Scheduler(config, now=frozen_clock.now, sleep=frozen_clock.sleep),
        schedule_service=FakeScheduleService([]),
        booking_service=FakeBookingService(
            BookingResult(success=False, attempts=0, slot_id=None)
        ),
    )

    with pytest.raises(ValueError, match="Could not resolve full doctor page target"):
        await runner.run()


@pytest.mark.asyncio
async def test_runner_fails_when_dep_id_stays_placeholder_zero(frozen_clock):
    unresolved_target = DoctorPageTarget(
        unit_id="131",
        dept_id="0",
        doctor_id="200254692",
        source_url="https://www.91160.com/doctors/index/unit_id-131/dep_id-0/docid-200254692.html",
        needs_resolution=True,
    )
    config = GrabConfig(
        enable_appoint=True,
        appoint_time=frozen_clock.now() + timedelta(seconds=15),
    )
    runner = GrabRunner(
        auth_service=FakeAuthService(),
        session_service=FakeSessionService(target=unresolved_target, config=config),
        scheduler=Scheduler(config, now=frozen_clock.now, sleep=frozen_clock.sleep),
        schedule_service=FakeScheduleService([]),
        booking_service=FakeBookingService(
            BookingResult(success=False, attempts=0, slot_id=None)
        ),
    )

    with pytest.raises(ValueError, match="Could not resolve full doctor page target"):
        await runner.run()


@pytest.mark.asyncio
async def test_runner_emits_run_failed_event_on_unresolved_target(frozen_clock):
    unresolved_target = DoctorPageTarget(
        unit_id=None,
        dept_id=None,
        doctor_id="14765",
        source_url="https://www.91160.com/doctors/index/docid-14765.html",
        needs_resolution=True,
    )
    reporter = FakeReporter()
    config = GrabConfig(
        enable_appoint=True,
        appoint_time=frozen_clock.now() + timedelta(seconds=15),
    )
    runner = GrabRunner(
        auth_service=FakeAuthService(),
        session_service=FakeSessionService(target=unresolved_target, config=config),
        scheduler=Scheduler(config, now=frozen_clock.now, sleep=frozen_clock.sleep),
        schedule_service=FakeScheduleService([]),
        booking_service=FakeBookingService(
            BookingResult(success=False, attempts=0, slot_id=None)
        ),
        reporter=reporter,
    )

    with pytest.raises(ValueError, match="Could not resolve full doctor page target"):
        await runner.run()

    assert reporter.events[-1]["event"] == "run_failed"
    assert reporter.events[-1]["notify"] is True
    assert reporter.events[-1]["data"]["phase"] == "resolve_target"


@pytest.mark.asyncio
async def test_runner_recovers_from_session_expiry_by_restarting_manual_login(
    frozen_clock,
):
    config = GrabConfig(
        enable_appoint=True,
        appoint_time=frozen_clock.now() + timedelta(seconds=15),
    )
    auth_service = FakeAuthService()
    session_service = FakeSessionService(config=config)
    schedule_service = RecoveringScheduleService(
        [Slot(schedule_id="sch-1001", doctor_id="14765", time_range="08:00-08:30")]
    )
    booking_service = FakeBookingService(
        BookingResult(success=True, attempts=1, slot_id="sch-1001")
    )
    runner = GrabRunner(
        auth_service=auth_service,
        session_service=session_service,
        scheduler=Scheduler(config, now=frozen_clock.now, sleep=frozen_clock.sleep),
        schedule_service=schedule_service,
        booking_service=booking_service,
    )

    result = await runner.run()

    assert result.success is True
    assert auth_service.calls == 2
    assert session_service.capture_calls == 2
    assert session_service.member_calls == 2
    assert schedule_service.poll_calls == 2


@pytest.mark.asyncio
async def test_runner_stops_after_session_recovery_limit_and_applies_cooldown(
    frozen_clock,
):
    cooldown_calls: list[int] = []

    async def fake_sleep(seconds: int):
        cooldown_calls.append(seconds)

    config = GrabConfig(
        enable_appoint=True,
        appoint_time=frozen_clock.now() + timedelta(seconds=15),
        browser={
            "session_recovery_max_attempts": 2,
            "session_recovery_cooldown_seconds": 7,
        },
    )
    auth_service = FakeAuthService()
    session_service = FakeSessionService(config=config)
    runner = GrabRunner(
        auth_service=auth_service,
        session_service=session_service,
        scheduler=Scheduler(config, now=frozen_clock.now, sleep=frozen_clock.sleep),
        schedule_service=AlwaysExpiredScheduleService([]),
        booking_service=FakeBookingService(
            BookingResult(success=False, attempts=0, slot_id=None)
        ),
        sleep=fake_sleep,
    )

    with pytest.raises(
        RuntimeError,
        match="Session recovery attempts exceeded the configured limit",
    ):
        await runner.run()

    assert auth_service.calls == 3
    assert session_service.capture_calls == 3
    assert session_service.member_calls == 3
    assert cooldown_calls == [7]


@pytest.mark.parametrize("state", ["OUTCOME_UNKNOWN", "AWAITING_MANUAL_CONFIRMATION"])
async def test_runner_stops_polling_on_manual_or_unknown(state):
    from grab.models.schemas import BookingState

    seen = []

    class Schedule:
        async def poll(self):
            for _ in range(3):
                seen.append(True)
                yield [Slot(schedule_id="synthetic")]

    runner = GrabRunner(
        None,
        None,
        None,
        Schedule(),
        FakeBookingService(BookingResult(state=BookingState(state))),
    )
    result = await runner._poll_and_book()
    assert result.state == state
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_unknown_schema_stops_for_manual_inspection_without_relogin(runner):
    from grab.errors import UnknownSessionError

    async def poll():
        raise UnknownSessionError("schema_drift")
        yield []

    runner.schedule_service.poll = poll
    result = await runner.run()
    assert result.state == BookingState.AWAITING_MANUAL_CONFIRMATION
    assert result.failure_class == "schema_drift"
    assert runner.auth_service.calls == 1


@pytest.mark.asyncio
async def test_pending_during_recovery_blocks_new_login(runner):
    from grab.errors import SessionExpiredError
    from grab.models.schemas import BookingResult

    runner.booking_service.blocked_result = lambda: BookingResult(
        state=BookingState.OUTCOME_UNKNOWN
    )
    from grab.core.leader import leader_scope

    async with leader_scope():
        result = await runner._recover_from_session_expiry(
            SessionExpiredError(), attempt=1
        )
    assert result.state == BookingState.OUTCOME_UNKNOWN
    assert runner.auth_service.calls == 0


async def test_runner_missed_start_stops_without_poll_or_booking(runner, frozen_clock):
    frozen_clock.current += timedelta(seconds=46)
    runner.scheduler.is_interactive = False
    result = await runner.run()
    assert result.state == BookingState.AWAITING_MANUAL_CONFIRMATION
    assert runner.schedule_service.poll_calls == 0
    assert runner.booking_service.calls == 0
