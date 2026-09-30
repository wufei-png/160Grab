import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from grab.canary.policy import CanaryPolicy, canary_scope
from grab.canary.runner import CanaryRunner
from grab.models.schemas import BookingForm, DoctorPageTarget, GrabConfig, Slot


def setup_runner(**kwargs):
    target = DoctorPageTarget(unit_id="u", dept_id="d", doctor_id="doc", source_url="")
    session = SimpleNamespace(
        _wait_for_doctor_target=AsyncMock(return_value=target),
        resolve_unit_dept_ids=AsyncMock(return_value=target),
    )
    slot = Slot(
        schedule_id="slot",
        doctor_id="doc",
        unit_id="u",
        dep_id="d",
        date="2026-09-30",
        status="available",
    )
    schedule = SimpleNamespace(
        set_target=Mock(), poll_once=AsyncMock(return_value=[slot])
    )
    form = BookingForm(member_id="member", schedule_id="slot", schedule_date=slot.date)
    booking = SimpleNamespace(
        prepare=Mock(),
        blocked_result=Mock(return_value=None),
        open_booking_form=AsyncMock(return_value=form),
        submit_open_form=AsyncMock(
            return_value=SimpleNamespace(state="OUTCOME_UNKNOWN")
        ),
    )
    config = GrabConfig(
        doctor_ids=["doc"],
        member_id="member",
        hours=["9-10"],
        brush_start_date="2026-09-30",
    )
    runner = CanaryRunner(
        session, schedule, booking, config, ready=AsyncMock(return_value=True), **kwargs
    )
    return runner


@pytest.mark.parametrize("level", ["readonly", "prepare"])
async def test_lower_levels_never_submit_with_valid_live_flags(level):
    runner = setup_runner(live_e2e=True, live_booking=True)
    result = await runner.run(level)
    assert result.submit_calls == 0
    runner.booking.submit_open_form.assert_not_called()
    if level == "readonly":
        runner.booking.prepare.assert_not_called()
        runner.booking.open_booking_form.assert_not_called()
    else:
        assert result.status == "prepared"


@pytest.mark.parametrize("e2e,booking", [(False, False), (True, False), (False, True)])
async def test_submit_requires_both_live_flags_before_any_action(e2e, booking):
    runner = setup_runner(live_e2e=e2e, live_booking=booking)
    assert (await runner.run("submit")).status == "submit_gate_blocked"
    runner.ready.assert_not_called()


async def test_submit_exact_approval_and_unknown_returned_honestly():
    runner = setup_runner(live_e2e=True, live_booking=True)
    result = await runner.run("submit")
    assert result.state == "OUTCOME_UNKNOWN" and result.submit_calls == 1
    assert [c.args[0] for c in runner.ready.call_args_list] == [
        "ready",
        "prepare",
        "submit",
    ]


async def test_rejected_specific_approval_does_not_prepare():
    runner = setup_runner()
    runner.ready.side_effect = [True, False]
    assert (await runner.run("prepare")).status == "prepare_not_approved"
    runner.booking.open_booking_form.assert_not_called()


async def test_docid_only_resolution_before_poll():
    runner = setup_runner()
    runner.session._wait_for_doctor_target.return_value = DoctorPageTarget(
        doctor_id="doc", source_url="", needs_resolution=True
    )
    assert (await runner.run("readonly")).status == "observed"
    runner.session.resolve_unit_dept_ids.assert_awaited_once()


async def test_unresolved_target_and_pending_stop_before_poll():
    runner = setup_runner()
    runner.session._wait_for_doctor_target.return_value.dept_id = "0"
    assert (await runner.run("readonly")).status == "target_unresolved"
    runner.schedule.poll_once.assert_not_called()
    runner = setup_runner()
    runner.booking.blocked_result.return_value = True
    assert (await runner.run("prepare")).status == "pending_blocked"
    runner.schedule.poll_once.assert_not_called()


async def test_no_slots_exhausts_count_budget(monkeypatch):
    runner = setup_runner(max_polls=2)
    runner.schedule.poll_once.return_value = []
    monkeypatch.setattr("grab.canary.runner.asyncio.sleep", AsyncMock())
    result = await runner.run("readonly")
    assert result.status == "inconclusive_no_slots" and result.polls == 2
    runner.booking.open_booking_form.assert_not_called()


async def test_ready_is_bounded_and_cancellation_propagates():
    runner = setup_runner(timeout_seconds=1)

    async def wait(*_):
        await asyncio.Event().wait()

    runner.ready = wait
    assert (await runner.run("readonly")).status == "inconclusive_timeout"
    task = asyncio.create_task(runner.run("readonly"))
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_policy_guards_direct_product_calls(chromium_page, tmp_path):
    from grab.services.booking import PageBookingStrategy
    from grab.transactions.store import AttemptStore

    page = chromium_page
    await page.set_content(
        '<input name="schedule_id" value="slot"><input type="hidden" name="mid" value="member"><button id="submitbtn">预约</button>'
    )
    await page.evaluate(
        "() => { window.clicks=0;document.querySelector('button').onclick=()=>clicks++; }"
    )
    strategy = PageBookingStrategy(
        page,
        GrabConfig(),
        attempt_store=AttemptStore(tmp_path),
        authorization=lambda *_: True,
    )
    strategy.prepare(
        DoctorPageTarget(unit_id="u", dept_id="d", doctor_id="doc", source_url=""),
        "member",
    )
    for level in ("readonly", "prepare"):
        with canary_scope(CanaryPolicy(level, True, True, True)):
            result = await strategy.submit_open_form(
                BookingForm(member_id="member", schedule_id="slot")
            )
            assert result.state == "AWAITING_MANUAL_CONFIRMATION"
    assert await page.evaluate("clicks") == 0
    assert not strategy.attempt_store.pending()
