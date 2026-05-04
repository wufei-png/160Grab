import asyncio
import os
import sys
from pathlib import Path

from loguru import logger

from grab.browser.page_api import BrowserPageApi
from grab.browser.playwright_client import PlaywrightClient
from grab.core.runner import GrabRunner
from grab.core.scheduler import Scheduler
from grab.services.auth import AuthService
from grab.services.booking import BookingService, PageBookingStrategy
from grab.services.schedule import ScheduleService
from grab.services.session import SessionCaptureService
from grab.utils.config_loader import load_config


def setup_logging(verbose: bool = False) -> None:
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=level)


def _optional_path_env(name: str) -> Path | None:
    value = os.getenv(name)
    if not value:
        return None
    return Path(value).expanduser()


async def main() -> None:
    logger.info("160Grab - 健康160自动挂号")
    config_path = Path(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
    config = load_config(config_path)
    headless = config.auth.strategy != "manual"
    debug_dir = _optional_path_env("GRAB_DEBUG_DIR")

    async with PlaywrightClient(headless=headless, debug_dir=debug_dir) as client:
        try:
            runner = build_runner(config, client)
            result = await runner.run()
        except Exception as exc:
            logger.error(f"Run failed: {exc}")
            raise SystemExit(1) from None

        raise SystemExit(0 if result.success else 1)


def build_runner(config, client: PlaywrightClient) -> GrabRunner:
    if client.page is None:
        raise RuntimeError("Playwright page is not initialized")

    page_api = BrowserPageApi(client.page)
    auth_service = AuthService(client.page, config, notify=logger.info)
    session_service = SessionCaptureService(
        client.page,
        config,
        debug_snapshot=client.capture_snapshot,
        debug_state_provider=client.collect_debug_state,
    )
    scheduler = Scheduler(config)
    schedule_service = ScheduleService(page_api, config=config, sleep=asyncio.sleep)
    booking_service = BookingService(page_strategy=PageBookingStrategy(client.page))
    return GrabRunner(
        auth_service,
        session_service,
        scheduler,
        schedule_service,
        booking_service,
    )


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
