import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from grab.core.scheduler import Scheduler
from grab.models.schemas import GrabConfig
from grab.services.schedule import ScheduleService


def configured(instant, timezone="Asia/Shanghai", grace=30):
    return GrabConfig(
        enable_appoint=True,
        appoint_time=instant,
        schedule={"timezone": timezone, "late_start_grace_seconds": grace},
    )


def test_naive_migrates_in_configured_zone_with_safe_warning():
    with pytest.warns(UserWarning, match="migrate to an offset ISO"):
        config = configured("2030-01-01T08:00:00")
    assert config.appoint_time == datetime(2030, 1, 1, tzinfo=UTC)
    other = configured("2030-01-01T03:00:00-05:00")
    assert other.appoint_time == datetime(2030, 1, 1, 8, tzinfo=UTC)


@pytest.mark.parametrize("instant", ["2026-11-01T01:30:00", "2026-03-08T02:30:00"])
def test_dst_fold_and_gap_require_explicit_offset(instant):
    with pytest.raises(ValidationError, match="ambiguous or nonexistent"):
        configured(instant, "America/New_York")
    assert configured(instant + "-05:00", "America/New_York").appoint_time.tzinfo is UTC


@pytest.mark.parametrize(
    "schedule",
    [
        {"timezone": "invalid"},
        {"late_start_grace_seconds": -1},
        {"late_start_grace_seconds": float("inf")},
    ],
)
def test_invalid_time_config_rejected(schedule):
    with pytest.raises(ValidationError):
        GrabConfig(schedule=schedule)


@pytest.mark.parametrize("late,ready", [(0, True), (30, True), (30.001, False)])
async def test_late_boundary_noninteractive_never_prompts(late, ready):
    target = datetime(2030, 1, 1, tzinfo=UTC)
    scheduler = Scheduler(
        configured(target),
        now=lambda: target + timedelta(seconds=late),
        is_interactive=False,
        prompt_text=lambda _: pytest.fail("noninteractive prompt"),
    )
    assert await scheduler.wait_until_ready() is ready


@pytest.mark.parametrize("answer,ready", [("yes", True), ("n", False), ("", False)])
async def test_late_start_requires_fresh_confirmation(answer, ready):
    target = datetime(2030, 1, 1, tzinfo=UTC)
    prompts = []
    scheduler = Scheduler(
        configured(target),
        now=lambda: target + timedelta(minutes=1),
        is_interactive=True,
        prompt_text=lambda text: prompts.append(text) or answer,
    )
    assert await scheduler.wait_until_ready() is ready
    assert len(prompts) == 1


async def test_subsecond_wait_and_zone_independent_clock():
    target = datetime(2030, 1, 1, tzinfo=UTC)
    current = target - timedelta(seconds=5.25)
    delays = []

    async def sleep(seconds):
        nonlocal current
        assert seconds > 0
        delays.append(seconds)
        current += timedelta(seconds=seconds)

    scheduler = Scheduler(configured(target), now=lambda: current, sleep=sleep)
    assert await scheduler.wait_until_ready()
    assert delays == [5, 0.25]


async def test_clock_jump_after_wait_requires_confirmation():
    target = datetime(2030, 1, 1, tzinfo=UTC)
    current = target - timedelta(seconds=1)

    async def sleep(_seconds):
        nonlocal current
        current = target + timedelta(seconds=31)

    assert not await Scheduler(
        configured(target), now=lambda: current, sleep=sleep, is_interactive=False
    ).wait_until_ready()


async def test_cancel_propagates_without_start():
    target = datetime(2030, 1, 1, tzinfo=UTC)
    entered = asyncio.Event()

    async def sleep(_seconds):
        entered.set()
        await asyncio.Future()

    task = asyncio.create_task(
        Scheduler(
            configured(target), now=lambda: target - timedelta(seconds=1), sleep=sleep
        ).wait_until_ready()
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_schedule_date_uses_configured_zone_not_host_or_utc():
    instant = datetime(2030, 1, 1, 16, tzinfo=UTC)
    service = ScheduleService(None, GrabConfig(), now=lambda: instant)
    assert service._resolve_target_date() == "2030-01-02"
    service.config.schedule.timezone = "America/New_York"
    assert service._resolve_target_date() == "2030-01-01"
    service.config.brush_start_date = datetime(2030, 2, 2).date()
    assert service._resolve_target_date() == "2030-02-02"
