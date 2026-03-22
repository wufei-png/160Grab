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
        member_id = await self.session_service.resolve_member_id()
        self.schedule_service.set_target(target)
        self.booking_service.prepare(target, member_id)
        await self.scheduler.wait_until_ready()

        async for slots in self.schedule_service.poll():
            result: BookingResult = await self.booking_service.try_book_first_available(
                slots
            )
            if result.success:
                return RunResult(success=True, booked_slot_id=result.slot_id)

        return RunResult(success=False, booked_slot_id=None)
