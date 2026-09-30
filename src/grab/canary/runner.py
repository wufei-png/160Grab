import asyncio
from dataclasses import dataclass

from grab.canary.policy import CanaryPolicy, canary_scope, scenario
from grab.core.leader import check_leader, leader_scope
from grab.models.schemas import BookingState


@dataclass(frozen=True)
class CanaryResult:
    status: str
    level: str
    polls: int = 0
    form_opened: bool = False
    state: str | None = None
    submit_calls: int = 0


class CanaryRunner:
    """Manual ready callback is async and covered by the total wall-clock budget.

    No login automation, member discovery, recovery loop or unbounded poll owner.
    The operator logs in/navigates before ready. All result fields are safe enums
    and counts; callbacks may display the private scenario locally only.
    """

    def __init__(
        self,
        session,
        schedule,
        booking,
        config,
        *,
        ready,
        max_polls=3,
        timeout_seconds=120,
        live_e2e=False,
        live_booking=False,
    ):
        if not 1 <= max_polls <= 10 or not 1 <= timeout_seconds <= 600:
            raise ValueError("Invalid canary budget")
        self.session, self.schedule, self.booking = session, schedule, booking
        self.config, self.ready = config, ready
        self.max_polls, self.timeout_seconds = max_polls, timeout_seconds
        self.live_e2e, self.live_booking = live_e2e, live_booking

    async def run(self, level):
        if level not in {"readonly", "prepare", "submit"}:
            raise ValueError("Explicit canary level required")
        if level == "submit" and not (self.live_e2e and self.live_booking):
            return CanaryResult("submit_gate_blocked", level)
        if not self.config.doctor_ids or not self.config.brush_start_date:
            return CanaryResult("missing_target", level)
        if level != "readonly" and (not self.config.member_id or not self.config.hours):
            return CanaryResult("missing_scenario", level)
        policy = CanaryPolicy(level, self.live_e2e, self.live_booking)
        self.polls = 0
        self.form_opened = False
        self.submit_calls = 0
        try:
            async with leader_scope():
                with canary_scope(policy):
                    async with asyncio.timeout(self.timeout_seconds):
                        return await self._run(policy)
        except TimeoutError:
            return CanaryResult(
                "inconclusive_timeout",
                level,
                self.polls,
                self.form_opened,
                BookingState.OUTCOME_UNKNOWN if self.submit_calls else None,
                self.submit_calls,
            )

    async def _run(self, policy):
        level = policy.level
        if not await self.ready("ready", None):
            return CanaryResult("not_ready", level)
        check_leader()
        target = await self.session._wait_for_doctor_target()
        if target.needs_resolution:
            target = await self.session.resolve_unit_dept_ids(target)
        if (
            target.needs_resolution
            or not target.unit_id
            or not target.dept_id
            or target.unit_id == "0"
            or target.dept_id == "0"
            or target.doctor_id not in self.config.doctor_ids
        ):
            return CanaryResult("target_unresolved", level)
        self.schedule.set_target(target)
        # Reading the durable blocker applies even to preparation; never clear it.
        if self.booking.blocked_result():
            return CanaryResult(
                "pending_blocked", level, state=BookingState.OUTCOME_UNKNOWN
            )
        for index in range(self.max_polls):
            check_leader()
            self.polls += 1
            slots = await self.schedule.poll_once()
            slots = [
                s
                for s in slots
                if s.status == "available"
                and s.doctor_id == target.doctor_id
                and s.unit_id == target.unit_id
                and s.dep_id == target.dept_id
                and s.date == self.config.brush_start_date.isoformat()
            ]
            if slots:
                break
            if index + 1 < self.max_polls:
                # Preserve the normal lower pacing boundary, with a finite budget.
                from grab.utils.runtime import parse_sleep_time

                await asyncio.sleep(
                    max(3, parse_sleep_time(self.config.sleep_time) / 1000)
                )
        else:
            return CanaryResult("inconclusive_no_slots", level, self.polls)
        if level == "readonly":
            return CanaryResult("observed", level, self.polls)
        private = (target, slots[0], self.config)
        if not await self.ready("prepare", private):
            return CanaryResult("prepare_not_approved", level, self.polls)
        policy.prepare_approved = True
        self.booking.prepare(target, self.config.member_id)
        form = await self.booking.open_booking_form(slots[0])
        self.form_opened = True
        if not form.is_valid:
            return CanaryResult(
                "readiness_blocked",
                level,
                self.polls,
                True,
                BookingState.AWAITING_MANUAL_CONFIRMATION,
            )
        if level == "prepare":
            return CanaryResult(
                "prepared", level, self.polls, True, BookingState.PREPARED
            )
        if not await self.ready("submit", (target, form, self.config)):
            return CanaryResult("submit_not_approved", level, self.polls, True)
        policy.approved_submission = scenario(target, form)
        check_leader()
        self.submit_calls = 1
        result = await self.booking.submit_open_form(form)
        return CanaryResult(
            "submit_observed", level, self.polls, True, result.state, self.submit_calls
        )
