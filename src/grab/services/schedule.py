from datetime import date

from grab.models.schemas import DoctorPageTarget, GrabConfig, Slot
from grab.utils.runtime import parse_sleep_time


class ScheduleService:
    def __init__(self, page_api, config: GrabConfig | None = None, sleep=None):
        self.page_api = page_api
        self.config = config
        self._sleep = sleep
        self.target: DoctorPageTarget | None = None

    def set_target(self, target: DoctorPageTarget) -> None:
        self.target = target

    async def fetch_doctor_schedule(self, date: str) -> dict:
        if self.target is None:
            raise RuntimeError("Doctor page target has not been captured yet")
        return await self.page_api.get_json(
            "/guahao/v1/pc/sch/doctor",
            params={
                "unit_id": self.target.unit_id,
                "doctor_id": self.target.doctor_id,
                "date": date,
            },
        )

    def parse_doctor_schedule(self, payload: dict) -> list[Slot]:
        return self._parse_slots(payload)

    def filter_slots(
        self,
        slots: list[Slot],
        doctor_ids: list[str],
        weeks: list[int],
        days: list[str],
        hours: list[str],
    ) -> list[Slot]:
        return [
            slot
            for slot in slots
            if (not doctor_ids or slot.doctor_id in doctor_ids)
            and (not weeks or slot.weekday in weeks)
            and (not days or slot.day_period in days)
            and (not hours or slot.time_range in hours)
        ]

    def _parse_slots(self, payload: dict) -> list[Slot]:
        schedules = payload.get("data", {}).get("schedules", [])
        return [Slot.model_validate(schedule) for schedule in schedules]

    async def poll(self):
        if self.config is None:
            raise RuntimeError("ScheduleService config is required for polling")

        while True:
            slots = await self.poll_once()
            yield slots
            if self._sleep is not None:
                await self._sleep(parse_sleep_time(self.config.sleep_time) / 1000)

    async def poll_once(self) -> list[Slot]:
        if self.config is None:
            raise RuntimeError("ScheduleService config is required for polling")
        if self.target is None:
            raise RuntimeError("Doctor page target has not been captured yet")

        target_date = self._resolve_target_date()
        payload = await self.fetch_doctor_schedule(target_date)
        slots = self.parse_doctor_schedule(payload)

        return self.filter_slots(
            slots,
            doctor_ids=self.config.doctor_ids or [self.target.doctor_id],
            weeks=self.config.weeks,
            days=self.config.days,
            hours=self.config.hours,
        )

    async def poll_until_match(self) -> list[Slot]:
        async for slots in self.poll():
            if slots:
                return slots

        return []

    def _resolve_target_date(self) -> str:
        target = self.config.brush_start_date or date.today()
        return target.isoformat()
