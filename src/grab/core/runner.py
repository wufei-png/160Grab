from loguru import logger

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
    ):
        self.auth_service = auth_service
        self.session_service = session_service
        self.scheduler = scheduler
        self.schedule_service = schedule_service
        self.booking_service = booking_service
        self.reporter = reporter
        self.current_phase = "startup"

    def _set_phase(self, phase: str) -> None:
        self.current_phase = phase
        if self.reporter is not None:
            self.reporter.set_phase(phase)

    async def run(self) -> RunResult:
        try:
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

            self._set_phase("wait_until_ready")
            await self.scheduler.wait_until_ready()
            logger.info("Scheduler ready. Starting schedule polling.")

            self._set_phase("schedule_polling")
            async for slots in self.schedule_service.poll():
                if slots:
                    self._set_phase("booking")
                result: BookingResult = (
                    await self.booking_service.try_book_first_available(slots)
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
