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
    ):
        self.auth_service = auth_service
        self.session_service = session_service
        self.scheduler = scheduler
        self.schedule_service = schedule_service
        self.booking_service = booking_service

    async def run(self) -> RunResult:
        await self.auth_service.ensure_login()
        target = await self.session_service.capture_target_from_current_page()
        logger.info(
            "Captured doctor target: doctor_id={}, unit_id={}, dept_id={}, needs_resolution={}",
            target.doctor_id,
            target.unit_id,
            target.dept_id,
            target.needs_resolution,
        )
        if target.needs_resolution:
            target = await self.session_service.resolve_unit_dept_ids(target)
            logger.info(
                "Resolved doctor target: doctor_id={}, unit_id={}, dept_id={}, needs_resolution={}",
                target.doctor_id,
                target.unit_id,
                target.dept_id,
                target.needs_resolution,
            )
        if (
            target.needs_resolution
            or target.unit_id is None
            or target.dept_id is None
            or target.dept_id == "0"
        ):
            raise ValueError("Could not resolve full doctor page target from current page")
        member_id = await self.session_service.resolve_member_id()
        logger.info("Resolved member_id={}", member_id)
        self.schedule_service.set_target(target)
        self.booking_service.prepare(target, member_id)
        await self.scheduler.wait_until_ready()
        logger.info("Scheduler ready. Starting schedule polling.")

        async for slots in self.schedule_service.poll():
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

        return RunResult(success=False, booked_slot_id=None)
