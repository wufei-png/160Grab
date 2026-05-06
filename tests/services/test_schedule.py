import json
from pathlib import Path

import pytest

from grab.models.schemas import DoctorPageTarget, GrabConfig, Slot
from grab.services.schedule import ScheduleService


class FakePageApi:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.cookie_calls: list[tuple[str, str | None]] = []
        self.global_calls: list[str] = []
        self.ajax_calls: list[tuple[str, dict[str, str]]] = []

    async def get_json(self, path: str, params: dict[str, str]) -> dict:
        self.calls.append((path, params))
        return {"result_code": 1, "data": {"schedules": []}}

    async def get_json_via_page_ajax(self, path: str, params: dict[str, str]) -> dict:
        self.ajax_calls.append((path, params))
        return {"result_code": 1, "data": {"schedules": []}}

    async def get_cookie_value(
        self,
        name: str,
        domain_contains: str | None = None,
    ) -> str | None:
        self.cookie_calls.append((name, domain_contains))
        return "user-key-1"

    async def get_global_value(self, name: str):
        self.global_calls.append(name)
        return "user-key-from-page"


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
            dept_id="369",
            doctor_id="14765",
            source_url="https://www.91160.com/doctors/index/unit_id-21/dep_id-369/docid-14765.html",
        )
    )

    await schedule_service.fetch_doctor_schedule("2026-03-24")

    assert schedule_service.page_api.global_calls == ["_user_key"]
    assert schedule_service.page_api.cookie_calls == []
    assert schedule_service.page_api.ajax_calls == [
        (
            "https://gate.91160.com/guahao/v1/pc/sch/doctor",
            {
                "user_key": "user-key-from-page",
                "docid": "14765",
                "doc_id": "14765",
                "unit_id": "21",
                "dep_id": "369",
                "date": "2026-03-24",
                "days": "6",
            },
        )
    ]
    assert schedule_service.page_api.calls == []


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


def test_filter_slots_matches_hour_overlap_not_exact_equality(schedule_service):
    slots = [
        Slot(
            schedule_id="sch-1",
            doctor_id="doc-1",
            weekday=6,
            day_period="am",
            time_range="09:00-09:30",
            status="available",
        ),
        Slot(
            schedule_id="sch-2",
            doctor_id="doc-1",
            weekday=6,
            day_period="am",
            time_range="11:30-12:00",
            status="available",
        ),
    ]

    filtered = schedule_service.filter_slots(
        slots,
        doctor_ids=[],
        weeks=[],
        days=[],
        hours=["09:00-19:00"],
    )

    assert [slot.schedule_id for slot in filtered] == ["sch-1", "sch-2"]


def test_filter_slots_keeps_coarse_slots_for_booking_page_hour_filter(schedule_service):
    slots = [
        Slot(
            schedule_id="sch-coarse-1",
            doctor_id="doc-1",
            weekday=6,
            day_period="am",
            time_range="",
            status="available",
        )
    ]

    filtered = schedule_service.filter_slots(
        slots,
        doctor_ids=[],
        weeks=[],
        days=[],
        hours=["09:30-10:00"],
    )

    assert [slot.schedule_id for slot in filtered] == ["sch-coarse-1"]


def test_parse_doctor_schedule_supports_paiban_payload():
    service = ScheduleService(page_api=FakePageApi())
    service.set_target(
        DoctorPageTarget(
            unit_id="131",
            dept_id="369",
            doctor_id="200254692",
            source_url="https://www.91160.com/doctors/index/unit_id-131/dep_id-369/docid-200254692.html",
        )
    )
    payload = {
        "code": 1,
        "dates": {"2026-05-05": "二"},
        "sch": {
            "group-1": {
                "369_200254692_pm": {
                    "2026-05-05": {
                        "schedule_id": "sch-live-1",
                        "doctor_id": "200254692",
                        "unit_id": "131",
                        "dep_id": "369",
                        "to_date": "2026-05-05",
                        "y_state": 1,
                        "dep_name": "康复医学科门诊",
                    }
                }
            }
        },
    }

    slots = service.parse_doctor_schedule(payload)

    assert len(slots) == 1
    assert slots[0].schedule_id == "sch-live-1"
    assert slots[0].doctor_id == "200254692"
    assert slots[0].dep_id == "369"
    assert slots[0].weekday == 2
    assert slots[0].day_period == "pm"
    assert slots[0].status == "available"


class SequencedPageApi(FakePageApi):
    def __init__(self, responses: list[dict]):
        super().__init__()
        self.responses = list(responses)

    async def get_json(self, path: str, params: dict[str, str]) -> dict:
        self.calls.append((path, params))
        return self.responses.pop(0)

    async def get_json_via_page_ajax(self, path: str, params: dict[str, str]) -> dict:
        self.ajax_calls.append((path, params))
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_poll_backs_off_when_schedule_api_reports_rate_limit():
    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float):
        sleep_calls.append(seconds)

    service = ScheduleService(
        page_api=SequencedPageApi(
            [
                {"msg": "您单位时间内访问次数过多！"},
                {"result_code": 1, "data": {"schedules": []}},
            ]
        ),
        config=GrabConfig(
            sleep_time="3000",
            rate_limit_sleep_time="12000",
        ),
        sleep=fake_sleep,
    )
    service.set_target(
        DoctorPageTarget(
            unit_id="21",
            dept_id="369",
            doctor_id="14765",
            source_url="https://www.91160.com/doctors/index/unit_id-21/dep_id-369/docid-14765.html",
        )
    )

    poller = service.poll()
    slots = await anext(poller)
    await poller.aclose()

    assert slots == []
    assert sleep_calls == [12.0]
