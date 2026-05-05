import asyncio
import os
from dataclasses import dataclass
from typing import Literal

import pytest

from grab.browser.page_api import BrowserPageApi
from grab.browser.playwright_client import PlaywrightClient
from grab.core.scheduler import Scheduler
from grab.models.schemas import GrabConfig
from grab.services.auth import AuthService
from grab.services.booking import BookingService, PageBookingStrategy
from grab.services.schedule import ScheduleService
from grab.services.session import SessionCaptureService


def pytest_collection_modifyitems(config, items):
    if os.getenv("LIVE_E2E") == "1":
        return

    skip_live = pytest.mark.skip(reason="set LIVE_E2E=1 to run live tests")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


def _parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_int_csv(value: str | None) -> list[int]:
    return [int(item) for item in _parse_csv(value)]


def _build_live_config() -> GrabConfig:
    return GrabConfig(
        member_id=os.getenv("LIVE_MEMBER_ID"),
        doctor_ids=_parse_csv(os.getenv("LIVE_DOCTOR_IDS")),
        weeks=_parse_int_csv(os.getenv("LIVE_WEEKS")),
        days=_parse_csv(os.getenv("LIVE_DAYS")),
        hours=_parse_csv(os.getenv("LIVE_HOURS")),
        sleep_time=os.getenv("LIVE_SLEEP_TIME", "3000"),
        brush_start_date=os.getenv("LIVE_BRUSH_START_DATE"),
        booking_strategy="page",
        auth={"strategy": "manual"},
    )


@dataclass
class LiveRunResult:
    logged_in: bool
    schedule_checked: bool
    booking_form_opened: bool
    submitted: bool


class LiveRunner:
    def __init__(
        self,
        auth_service: AuthService,
        session_service: SessionCaptureService,
        schedule_service: ScheduleService,
        booking_service: BookingService,
        scheduler: Scheduler,
    ):
        self.auth_service = auth_service
        self.session_service = session_service
        self.schedule_service = schedule_service
        self.booking_service = booking_service
        self.scheduler = scheduler

    async def run(
        self,
        until: Literal["booking_confirmation", "final_submit"],
    ) -> LiveRunResult:
        login_result = await self.auth_service.ensure_login()
        if not login_result.success:
            raise RuntimeError("Live login failed")

        target = await self.session_service.capture_target_from_current_page()
        member_id = await self.session_service.resolve_member_id()
        self.schedule_service.set_target(target)
        self.booking_service.prepare(target, member_id)

        await self.scheduler.wait_until_ready()
        slots = await self.schedule_service.poll_until_match()
        if not slots:
            raise RuntimeError("No matching live slots found")

        form = await self.booking_service.open_booking_form(slots[0])
        if not form.is_valid:
            raise RuntimeError("Booking form is invalid")

        if until == "final_submit" and os.getenv("LIVE_BOOKING") == "1":
            submit_result = await self.booking_service.submit_open_form(form)
            return LiveRunResult(
                logged_in=True,
                schedule_checked=True,
                booking_form_opened=True,
                submitted=submit_result.success,
            )

        return LiveRunResult(
            logged_in=True,
            schedule_checked=True,
            booking_form_opened=True,
            submitted=False,
        )


@pytest.fixture
async def live_runner():
    config = _build_live_config()
    client = PlaywrightClient(headless=False)
    await client.launch()

    page_api = BrowserPageApi(client.page)
    auth_service = AuthService(client.page, config)
    session_service = SessionCaptureService(client.page, config)
    schedule_service = ScheduleService(page_api, config=config, sleep=asyncio.sleep)
    booking_service = BookingService(
        page_strategy=PageBookingStrategy(
            client.page,
            config=config,
            sleep=asyncio.sleep,
        )
    )
    scheduler = Scheduler(config)

    try:
        yield LiveRunner(
            auth_service=auth_service,
            session_service=session_service,
            schedule_service=schedule_service,
            booking_service=booking_service,
            scheduler=scheduler,
        )
    finally:
        await client.close()
