import asyncio
import os
import select
import sys

import pytest

from grab.browser.page_api import BrowserPageApi
from grab.browser.playwright_client import PlaywrightClient
from grab.canary.runner import CanaryRunner
from grab.core.leader import leader_scope
from grab.models.schemas import GrabConfig
from grab.services.booking import BookingService, PageBookingStrategy
from grab.services.schedule import ScheduleService
from grab.services.session import SessionCaptureService
from grab.transactions.consent import ConsentManager
from grab.transactions.store import AttemptStore
from grab.utils.profile_manager import load_profile


def pytest_collection_modifyitems(config, items):
    if os.getenv("LIVE_E2E") == "1":
        return
    for item in items:
        if "live" in item.keywords:
            item.add_marker(pytest.mark.skip(reason="set LIVE_E2E=1 to run live tests"))


def _build_live_config():
    return GrabConfig(
        member_id=os.getenv("LIVE_MEMBER_ID"),
        doctor_ids=[os.environ["LIVE_DOCTOR_ID"]],
        hours=[os.environ["LIVE_HOURS"]] if os.getenv("LIVE_HOURS") else [],
        brush_start_date=os.environ["LIVE_DATE"],
        booking={
            "clinic_card": os.getenv("LIVE_CARD"),
            "disease_description": os.getenv("LIVE_DISEASE"),
            "address": {
                k: os.getenv("LIVE_ADDRESS_" + k.upper())
                for k in ("province", "city", "area", "detail")
            },
        },
    )


async def terminal_ready(phase, private):
    # Nonblocking POSIX stdin, so timeout/cancellation also stops human waiting.
    # No executor thread left reading a future operator response after timeout.
    if not sys.stdin.isatty() or os.name != "posix":
        return False
    word = {"ready": "READY", "prepare": "PREPARE", "submit": "SUBMIT"}[phase]
    if private:
        target, item, config = private
        print(
            f"本次 {phase}：医生 {target.unit_id}/{target.dept_id}/{target.doctor_id}；"
            f"就诊人 {config.member_id}；日期 {config.brush_start_date}；"
            f"时段 {config.hours}；schedule {item.schedule_id}。",
            flush=True,
        )
        if phase == "prepare":
            print(
                "允许选择就诊人/时段及填写显式配置字段；请在本机核对全部字段。",
                flush=True,
            )
        else:
            print(
                f"最终时段值 {item.appointment_value}；将点击一次最终提交。"
                "未知结果必须在原站人工核对；禁止混跑。",
                flush=True,
            )
    print(
        f"手动登录并停留目标医生页（ready），或确认本次具体操作：输入 {word}：",
        flush=True,
    )
    while True:
        if select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.readline().strip() == word
        await asyncio.sleep(0.1)


@pytest.fixture
async def live_runner(request):
    required = ("LIVE_LEVEL", "LIVE_PROFILE", "LIVE_DOCTOR_ID", "LIVE_DATE")
    if not all(os.getenv(k) for k in required):
        pytest.skip("live blocker: explicit level/profile/doctor/date required")
    if os.getenv("LIVE_LEVEL") not in {"readonly", "prepare", "submit"}:
        pytest.fail("invalid LIVE_LEVEL", pytrace=False)
    if request.node.callspec.params["level"] != os.environ["LIVE_LEVEL"]:
        pytest.skip("different explicitly selected canary level")
    if os.environ["LIVE_LEVEL"] != "readonly" and not all(
        os.getenv(k) for k in ("LIVE_MEMBER_ID", "LIVE_HOURS")
    ):
        pytest.skip("live blocker: prepare/submit require member and time range")
    if not sys.stdin.isatty() or os.name != "posix":
        pytest.skip("live blocker: POSIX interactive terminal required; run pytest -s")
    if os.environ["LIVE_LEVEL"] == "submit" and os.getenv("LIVE_BOOKING") != "1":
        pytest.skip("live blocker: submit also requires LIVE_BOOKING=1")
    try:
        config = _build_live_config()
        profile = load_profile(
            os.getenv("LIVE_PROFILES_ROOT", "~/.160grab/browser-profiles"),
            os.environ["LIVE_PROFILE"],
        )
        budget = int(os.getenv("LIVE_MAX_POLLS", "3"))
        timeout = float(os.getenv("LIVE_TIMEOUT_SECONDS", "120"))
        if not 1 <= budget <= 10 or not 1 <= timeout <= 600:
            raise ValueError
    except Exception:
        pytest.skip("live blocker: unavailable profile or invalid scenario/budget")
    # Hold the production OS-user lock BEFORE launching the named profile.
    async with leader_scope():
        client = PlaywrightClient(
            headless=False, persistent_context_enabled=True, user_data_dir=profile.path
        )
        try:
            await asyncio.wait_for(client.launch(), 30)
            page = client.page
            await page.goto("https://www.91160.com/", timeout=20000)
            store = AttemptStore()
            consent = ConsentManager(
                store, interactive=True, prompt=lambda _: "AUTHORIZE"
            )
            strategy = PageBookingStrategy(
                page,
                config,
                attempt_store=store,
                consent_manager=consent,
                authorization=consent.ensure,
            )
            schedule = ScheduleService(
                BrowserPageApi(page), config, sleep=asyncio.sleep
            )
            session = SessionCaptureService(page, config)

            def change_page(next_page):
                strategy.page = next_page
                schedule.page_api.page = next_page

            session.on_page_change = change_page
            yield CanaryRunner(
                session,
                schedule,
                BookingService(strategy),
                config,
                ready=terminal_ready,
                max_polls=budget,
                timeout_seconds=timeout,
                live_e2e=True,
                live_booking=os.getenv("LIVE_BOOKING") == "1",
            )
        finally:
            await client.close()
