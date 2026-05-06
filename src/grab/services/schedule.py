import asyncio
import time
from datetime import date
from typing import Any

from loguru import logger

from grab.models.schemas import DoctorPageTarget, GrabConfig, Slot
from grab.utils.rate_limit import RateLimitError, raise_if_rate_limited
from grab.utils.runtime import parse_sleep_time


class ScheduleService:
    def __init__(
        self,
        page_api,
        config: GrabConfig | None = None,
        sleep=None,
        reporter=None,
        monotonic=None,
    ):
        self.page_api = page_api
        self.config = config
        self._sleep = sleep
        self.reporter = reporter
        self._monotonic = monotonic or time.monotonic
        self._last_heartbeat_at: float | None = None
        self.target: DoctorPageTarget | None = None

    def set_target(self, target: DoctorPageTarget) -> None:
        self.target = target

    async def fetch_doctor_schedule(self, date: str) -> dict:
        if self.target is None:
            raise RuntimeError("Doctor page target has not been captured yet")
        logger.info(
            "Fetching doctor schedule: doctor_id={}, unit_id={}, dep_id={}, date={}",
            self.target.doctor_id,
            self.target.unit_id,
            self.target.dept_id,
            date,
        )
        user_key = None
        if hasattr(self.page_api, "get_global_value"):
            user_key = await self.page_api.get_global_value("_user_key")
        if hasattr(self.page_api, "get_cookie_value"):
            user_key = user_key or await self.page_api.get_cookie_value(
                "access_hash",
                domain_contains="91160.com",
            )
        if not user_key:
            raise RuntimeError(
                "Could not find access_hash cookie required for doctor schedule polling"
            )
        fetch_json = getattr(self.page_api, "get_json_via_page_ajax", self.page_api.get_json)
        payload = await asyncio.wait_for(
            fetch_json(
                "https://gate.91160.com/guahao/v1/pc/sch/doctor",
                params={
                    "user_key": user_key,
                    "docid": self.target.doctor_id,
                    "doc_id": self.target.doctor_id,
                    "unit_id": self.target.unit_id,
                    "dep_id": self.target.dept_id,
                    "date": date,
                    "days": "6",
                },
            ),
            timeout=20,
        )
        raise_if_rate_limited(payload, context="doctor schedule polling")
        logger.info(
            "Doctor schedule response received: code={}, result_code={}, has_sch={}, has_data_schedules={}",
            payload.get("code"),
            payload.get("result_code"),
            "sch" in payload,
            bool(payload.get("data", {}).get("schedules")),
        )
        return payload

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
            and (not hours or self._slot_matches_hours(slot, hours))
        ]

    def _parse_slots(self, payload: dict) -> list[Slot]:
        schedules = payload.get("data", {}).get("schedules", [])
        if schedules:
            return [Slot.model_validate(schedule) for schedule in schedules]

        if "sch" in payload and self.target is not None:
            return self._parse_paiban_slots(payload)

        return []

    def _parse_paiban_slots(self, payload: dict) -> list[Slot]:
        slots: list[Slot] = []
        weekday_labels = payload.get("dates") or {}
        weekday_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7}

        for path, item in self._walk_schedule_tree(payload.get("sch"), path=()):
            date_key = next(
                (part for part in reversed(path) if isinstance(part, str) and len(part) == 10),
                item.get("to_date"),
            )
            half_key = next(
                (
                    part
                    for part in reversed(path)
                    if isinstance(part, str)
                    and part.endswith(("_am", "_pm", "_em"))
                ),
                "",
            )
            day_period = item.get("day_period") or (half_key.rsplit("_", 1)[-1] if half_key else "")
            weekday_label = weekday_labels.get(date_key, "")
            time_range = (
                item.get("time_range")
                or item.get("time_slot")
                or item.get("time_desc")
                or ""
            )
            status = self._map_paiban_status(item.get("y_state"))
            slots.append(
                Slot(
                    schedule_id=str(item.get("schedule_id") or ""),
                    doctor_id=str(item.get("doctor_id") or self.target.doctor_id),
                    weekday=weekday_map.get(str(weekday_label), 0),
                    day_period=str(day_period),
                    hospital=str(item.get("unit_name") or ""),
                    department=str(
                        item.get("schext_clinic_label")
                        or item.get("dep_name")
                        or ""
                    ),
                    doctor=str(item.get("doctor_name") or ""),
                    date=str(item.get("to_date") or date_key or ""),
                    time_range=str(time_range),
                    status=status,
                    unit_id=str(item.get("unit_id") or self.target.unit_id or ""),
                    dep_id=str(item.get("dep_id") or self.target.dept_id or ""),
                    doc_id=str(item.get("doc_id") or item.get("doctor_id") or self.target.doctor_id),
                )
            )
        return slots

    def _walk_schedule_tree(
        self,
        node: Any,
        path: tuple[str, ...],
    ) -> list[tuple[tuple[str, ...], dict[str, Any]]]:
        if not isinstance(node, dict):
            return []
        if "schedule_id" in node and "y_state" in node:
            return [(path, node)]

        results: list[tuple[tuple[str, ...], dict[str, Any]]] = []
        for key, value in node.items():
            results.extend(self._walk_schedule_tree(value, path + (str(key),)))
        return results

    def _map_paiban_status(self, y_state) -> str:
        return {
            1: "available",
            0: "full",
            -1: "expired",
            -2: "stopped",
            -3: "not_open",
        }.get(y_state, "unavailable")

    def _slot_matches_hours(self, slot: Slot, hours: list[str]) -> bool:
        if not slot.time_range:
            # Doctor schedule polling only exposes coarse availability blocks for some
            # 91160 flows; exact half-hour filtering is deferred to the booking page.
            return True
        slot_range = self._parse_time_range(slot.time_range)
        if slot_range is None:
            return False
        slot_start, slot_end = slot_range
        return any(
            self._time_ranges_overlap(slot_start, slot_end, filter_range)
            for filter_range in (
                self._parse_time_range(hour_filter) for hour_filter in hours
            )
            if filter_range is not None
        )

    def _parse_time_range(self, value: str) -> tuple[int, int] | None:
        if not value or "-" not in value:
            return None
        start_text, end_text = value.split("-", maxsplit=1)
        start_minutes = self._parse_time_to_minutes(start_text)
        end_minutes = self._parse_time_to_minutes(end_text)
        if start_minutes is None or end_minutes is None or start_minutes >= end_minutes:
            return None
        return start_minutes, end_minutes

    def _parse_time_to_minutes(self, value: str) -> int | None:
        parts = value.strip().split(":")
        if len(parts) != 2:
            return None
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError:
            return None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return hour * 60 + minute

    def _time_ranges_overlap(
        self,
        slot_start: int,
        slot_end: int,
        filter_range: tuple[int, int],
    ) -> bool:
        filter_start, filter_end = filter_range
        return slot_start < filter_end and slot_end > filter_start

    async def poll(self):
        if self.config is None:
            raise RuntimeError("ScheduleService config is required for polling")

        attempt = 0
        while True:
            attempt += 1
            delay_ms: int | None = None
            try:
                slots = await self.poll_once()
            except RateLimitError as exc:
                delay_ms = parse_sleep_time(self.config.rate_limit_sleep_time)
                logger.warning(
                    "Rate limit detected during schedule polling: {}. Cooling down for {} ms.",
                    exc.message,
                    delay_ms,
                )
                if self.reporter is not None:
                    await self.reporter.record_rate_limit(
                        context="schedule_polling",
                        message=exc.message,
                        data={
                            "attempt": attempt,
                            "cooldown_ms": delay_ms,
                        },
                    )
            else:
                if self.reporter is not None:
                    self.reporter.reset_rate_limit_streak()
                yield slots
                delay_ms = parse_sleep_time(self.config.sleep_time)
                logger.info(
                    "Polling attempt {} completed: {} matching slot(s). Next poll in {} ms.",
                    attempt,
                    len(slots),
                    delay_ms,
                )
                if self.reporter is not None:
                    await self.reporter.emit_event(
                        "schedule_poll_completed",
                        level="info",
                        message=(
                            f"Polling attempt {attempt} completed with "
                            f"{len(slots)} matching slot(s)."
                        ),
                        data={
                            "attempt": attempt,
                            "matching_slots": len(slots),
                            "next_poll_delay_ms": delay_ms,
                            "target_date": self._resolve_target_date(),
                        },
                    )
                    if self._should_emit_heartbeat():
                        await self.reporter.emit_event(
                            "schedule_poll_heartbeat",
                            level="info",
                            message=(
                                f"Still polling schedules after {attempt} attempt(s)."
                            ),
                            data={
                                "attempt": attempt,
                                "matching_slots": len(slots),
                            },
                        )
                        self._last_heartbeat_at = self._monotonic()

            if self._sleep is not None and delay_ms is not None:
                await self._sleep(delay_ms / 1000)

    async def poll_once(self) -> list[Slot]:
        if self.config is None:
            raise RuntimeError("ScheduleService config is required for polling")
        if self.target is None:
            raise RuntimeError("Doctor page target has not been captured yet")

        target_date = self._resolve_target_date()
        payload = await self.fetch_doctor_schedule(target_date)
        raw_slots = self.parse_doctor_schedule(payload)
        filtered_slots = self.filter_slots(
            raw_slots,
            doctor_ids=self.config.doctor_ids or [self.target.doctor_id],
            weeks=self.config.weeks,
            days=self.config.days,
            hours=self.config.hours,
        )
        logger.info(
            "Polling parse result: {} raw slot(s), {} filtered slot(s), filters weeks={}, days={}, hours={}",
            len(raw_slots),
            len(filtered_slots),
            self.config.weeks,
            self.config.days,
            self.config.hours,
        )
        if self.config.hours and raw_slots and not filtered_slots:
            logger.info(
                "Hour filter mismatch details: raw slot time ranges={}",
                sorted({slot.time_range for slot in raw_slots}),
            )

        return filtered_slots

    async def poll_until_match(self) -> list[Slot]:
        async for slots in self.poll():
            if slots:
                return slots

        return []

    def _resolve_target_date(self) -> str:
        target = self.config.brush_start_date or date.today()
        return target.isoformat()

    def _should_emit_heartbeat(self) -> bool:
        if self.config is None:
            return False
        if self._last_heartbeat_at is None:
            self._last_heartbeat_at = self._monotonic()
            return False
        elapsed = self._monotonic() - self._last_heartbeat_at
        return elapsed >= self.config.logging.heartbeat_interval_seconds
