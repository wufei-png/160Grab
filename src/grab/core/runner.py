import asyncio

from grab.core.leader import LeaderLost, check_leader, exclusive_operation
from grab.errors import SessionExpiredError, UnknownSessionError
from grab.models.schemas import BookingResult, BookingState, RunResult
from grab.observability.safe_logging import logger


class GrabRunner:
    def __init__(
        self,
        auth_service,
        session_service,
        scheduler,
        schedule_service,
        booking_service,
        reporter=None,
        sleep=None,
    ):
        self.auth_service = auth_service
        self.session_service = session_service
        self.scheduler = scheduler
        self.schedule_service = schedule_service
        self.booking_service = booking_service
        self.reporter = reporter
        self._sleep = sleep or asyncio.sleep
        self.current_phase = "startup"

    def _set_phase(self, phase: str) -> None:
        self.current_phase = phase
        if self.reporter is not None:
            self.reporter.set_phase(phase)

    async def _ensure_login_and_prepare_target(self) -> None:
        check_leader()
        self._set_phase("manual_login")
        await self.auth_service.ensure_login()

        self._set_phase("capture_target")
        target = await self.session_service.capture_target_from_current_page()
        if target.needs_resolution:
            self._set_phase("resolve_target")
            target = await self.session_service.resolve_unit_dept_ids(target)
        if (
            target.needs_resolution
            or target.unit_id is None
            or target.dept_id is None
            or target.dept_id == "0"
        ):
            raise ValueError(
                "Could not resolve full doctor page target from current page"
            )
        logger.info("Captured doctor target.")
        if self.reporter is not None:
            await self.reporter.emit_event(
                "target_captured",
                level="info",
                message="Captured doctor target from current page.",
                data={
                    "doctor_id": target.doctor_id,
                    "unit_id": target.unit_id,
                    "dept_id": target.dept_id,
                    "needs_resolution": target.needs_resolution,
                    "source_url": target.source_url,
                },
            )

        self._set_phase("resolve_member")
        member_id = await self.session_service.resolve_member_id()
        logger.info("Member selection resolved.")
        if self.reporter is not None:
            await self.reporter.emit_event(
                "member_resolved",
                level="info",
                message="Resolved member_id for current run.",
                data={"member_id": member_id},
            )

        self.schedule_service.set_target(target)
        self.booking_service.prepare(target, member_id)

    @exclusive_operation
    async def _poll_and_book(self) -> RunResult:
        self._set_phase("schedule_polling")
        async for slots in self.schedule_service.poll():
            check_leader()
            if slots:
                self._set_phase("booking")
            result: BookingResult = await self.booking_service.try_book_first_available(
                slots
            )
            if result.state not in {
                BookingState.DISCOVERED,
                BookingState.CONFIRMED_NO_EFFECT,
            }:
                logger.info("Run finished.")
                return RunResult(
                    state=result.state,
                    booked_slot_id=result.slot_id,
                    failure_class=result.failure_class,
                )
            self._set_phase("schedule_polling")

        return RunResult(state=BookingState.CONFIRMED_NO_EFFECT, booked_slot_id=None)

    async def _recover_from_session_expiry(
        self,
        exc: SessionExpiredError,
        *,
        attempt: int,
    ) -> RunResult | None:
        check_leader()
        logger.warning("Session expired during schedule polling.")
        if self.reporter is not None:
            await self.reporter.emit_event(
                "session_recovery_required",
                level="warning",
                message=(
                    "Schedule polling lost the authenticated session. Manual re-login is required."
                ),
                data={"error": str(exc), "attempt": attempt},
                notify=True,
                notification_title="160Grab 登录态失效",
                notification_severity="warning",
            )
        cooldown_seconds = self._get_session_recovery_cooldown_seconds()
        if attempt > 1 and cooldown_seconds > 0:
            logger.warning("Rate limit cooldown started.")
            await self._sleep(cooldown_seconds)
        check = getattr(self.booking_service, "blocked_result", None)
        blocked = check() if check else None
        if blocked:
            return RunResult(state=blocked.state, failure_class=blocked.failure_class)
        await self._ensure_login_and_prepare_target()
        logger.info("Session recovered; resuming schedule polling.")
        return None

    def _get_session_recovery_max_attempts(self) -> int:
        return self.session_service.config.browser.session_recovery_max_attempts

    def _get_session_recovery_cooldown_seconds(self) -> int:
        return self.session_service.config.browser.session_recovery_cooldown_seconds

    @exclusive_operation
    async def run(self) -> RunResult:
        try:
            check = getattr(self.booking_service, "blocked_result", None)
            blocked = check() if check else None
            if blocked:
                return RunResult(
                    state=blocked.state, failure_class=blocked.failure_class
                )
            check_leader()
            await self._ensure_login_and_prepare_target()

            self._set_phase("wait_until_ready")
            if await self.scheduler.wait_until_ready() is False:
                return RunResult(state=BookingState.AWAITING_MANUAL_CONFIRMATION)
            logger.info("Scheduler ready. Starting schedule polling.")

            session_recovery_attempts = 0
            while True:
                try:
                    return await self._poll_and_book()
                except SessionExpiredError as exc:
                    session_recovery_attempts += 1
                    max_attempts = self._get_session_recovery_max_attempts()
                    if session_recovery_attempts > max_attempts:
                        raise RuntimeError(
                            "Session recovery attempts exceeded the configured limit "
                            f"({max_attempts})."
                        ) from exc
                    recovered = await self._recover_from_session_expiry(
                        exc,
                        attempt=session_recovery_attempts,
                    )
                    if recovered:
                        return recovered
        except UnknownSessionError as exc:
            if self.reporter is not None:
                await self.reporter.emit_event(
                    "session_inspection_required",
                    level="error",
                    message="Session requires manual inspection.",
                    data={"failure_class": exc.failure_class},
                    notify=True,
                    notification_title="160Grab 会话需要人工核对",
                    notification_severity="error",
                )
            return RunResult(
                state=BookingState.AWAITING_MANUAL_CONFIRMATION,
                failure_class=exc.failure_class,
            )
        except LeaderLost:
            return RunResult(state=BookingState.AWAITING_MANUAL_CONFIRMATION)
        except Exception as exc:
            if self.reporter is not None:
                await self.reporter.emit_event(
                    "run_failed",
                    level="error",
                    message=f"Run failed during phase {self.current_phase}: {exc}",
                    data={
                        "phase": self.current_phase,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                    notify=True,
                    notification_title="160Grab 运行失败",
                    notification_severity="error",
                )
            raise
