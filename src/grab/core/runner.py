import asyncio

from loguru import logger

from grab.errors import SessionExpiredError
from grab.models.schemas import BookingResult, RunResult


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
            raise ValueError("Could not resolve full doctor page target from current page")
        logger.info(
            "Captured doctor target: doctor_id={}, unit_id={}, dept_id={}, needs_resolution={}",
            target.doctor_id,
            target.unit_id,
            target.dept_id,
            target.needs_resolution,
        )
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
        logger.info("Resolved member_id={}", member_id)
        if self.reporter is not None:
            await self.reporter.emit_event(
                "member_resolved",
                level="info",
                message="Resolved member_id for current run.",
                data={"member_id": member_id},
            )

        self.schedule_service.set_target(target)
        self.booking_service.prepare(target, member_id)

    async def _poll_and_book(self) -> RunResult:
        self._set_phase("schedule_polling")
        async for slots in self.schedule_service.poll():
            if slots:
                self._set_phase("booking")
            result: BookingResult = await self.booking_service.try_book_first_available(
                slots
            )
            if result.success:
                logger.info(
                    "Booking succeeded. booked_slot_id={}, attempts={}",
                    result.slot_id,
                    result.attempts,
                )
                return RunResult(success=True, booked_slot_id=result.slot_id)
            self._set_phase("schedule_polling")

        return RunResult(success=False, booked_slot_id=None)

    async def _recover_from_session_expiry(
        self,
        exc: SessionExpiredError,
        *,
        attempt: int,
    ) -> None:
        logger.warning(
            "Session expired during schedule polling: {}. Starting recovery attempt {}.",
            exc,
            attempt,
        )
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
            logger.warning(
                "Cooling down for {} second(s) before session recovery attempt {}.",
                cooldown_seconds,
                attempt,
            )
            await self._sleep(cooldown_seconds)
        await self._ensure_login_and_prepare_target()
        logger.info("Session recovered on attempt {}. Resuming schedule polling.", attempt)

    def _get_session_recovery_max_attempts(self) -> int:
        return self.session_service.config.browser.session_recovery_max_attempts

    def _get_session_recovery_cooldown_seconds(self) -> int:
        return self.session_service.config.browser.session_recovery_cooldown_seconds

    async def run(self) -> RunResult:
        try:
            await self._ensure_login_and_prepare_target()

            self._set_phase("wait_until_ready")
            await self.scheduler.wait_until_ready()
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
                    await self._recover_from_session_expiry(
                        exc,
                        attempt=session_recovery_attempts,
                    )
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
