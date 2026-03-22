import json
from pathlib import Path

import pytest

from grab.models.schemas import DoctorPageTarget
from grab.services.schedule import ScheduleService


class FakePageApi:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def get_json(self, path: str, params: dict[str, str]) -> dict:
        self.calls.append((path, params))
        return {"result_code": 1, "data": {"schedules": []}}


@pytest.fixture
def channel_2_payload():
    return json.loads(Path("tests/fixtures/channel_2_schedule.json").read_text())


@pytest.fixture
def schedule_service():
    return ScheduleService(page_api=FakePageApi())


@pytest.mark.asyncio
async def test_fetch_doctor_schedule_uses_doctor_endpoint_only(schedule_service):
    schedule_service.set_target(
        DoctorPageTarget(
            unit_id="21",
            dept_id="0",
            doctor_id="14765",
            source_url="https://www.91160.com/doctors/index/unit_id-21/dep_id-0/docid-14765.html",
        )
    )

    await schedule_service.fetch_doctor_schedule("2026-03-24")

    assert schedule_service.page_api.calls == [
        (
            "/guahao/v1/pc/sch/doctor",
            {"unit_id": "21", "doctor_id": "14765", "date": "2026-03-24"},
        )
    ]


def test_filter_slots_by_week_day_and_hour(schedule_service, channel_2_payload):
    slots = schedule_service.parse_doctor_schedule(channel_2_payload)

    filtered = schedule_service.filter_slots(
        slots,
        doctor_ids=["doc-3"],
        weeks=[3],
        days=["pm"],
        hours=["14:00-14:30"],
    )

    assert [slot.schedule_id for slot in filtered] == ["sch-2001"]
