import argparse
import asyncio
import os
import sys
from pathlib import Path

from loguru import logger

from grab.browser.page_api import BrowserPageApi
from grab.browser.playwright_client import PlaywrightClient
from grab.core.runner import GrabRunner
from grab.core.scheduler import Scheduler
from grab.models.schemas import GrabConfig
from grab.services.auth import AuthService
from grab.services.booking import BookingService, PageBookingStrategy
from grab.services.schedule import ScheduleService
from grab.services.session import SessionCaptureService
from grab.utils.config_loader import load_config
from grab.utils.config_writer import write_browser_profile_name
from grab.utils.profile_manager import (
    BrowserProfile,
    create_profile,
    resolve_profile_for_run,
)


def setup_logging(verbose: bool = False) -> None:
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=level)


def _optional_path_env(name: str) -> Path | None:
    value = os.getenv(name)
    if not value:
        return None
    return Path(value).expanduser()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="160Grab CLI")
    parser.add_argument("config_path", nargs="?", default="config.yaml")
    parser.add_argument(
        "--create-profile",
        action="store_true",
        help="Create a new persistent browser profile and open it for warm-up.",
    )
    parser.add_argument(
        "--profile-name",
        help="Explicit profile name for --create-profile.",
    )
    args = parser.parse_args(argv)
    if args.profile_name and not args.create_profile:
        parser.error("--profile-name can only be used together with --create-profile")
    return args


async def main(argv: list[str] | None = None) -> None:
    logger.info("160Grab - 健康160自动挂号")
    args = parse_args(argv)
    config_path = Path(args.config_path).expanduser()
    config = load_config(config_path)
    headless = config.auth.strategy != "manual"
    debug_dir = _optional_path_env("GRAB_DEBUG_DIR")
    if args.create_profile:
        await run_create_profile_flow(
            config=config,
            requested_profile_name=args.profile_name,
            debug_dir=debug_dir,
        )
        raise SystemExit(0)

    selected_profile: BrowserProfile | None = None
    profile_source = "transient"
    if config.browser.launch_persistent_context:
        resolved_profile = resolve_profile_for_run(
            root_dir=config.browser.profiles_root_dir,
            configured_profile_name=config.browser.profile_name,
            config_path=config_path,
            prompt_text=input,
            notify=print,
            is_interactive=sys.stdin.isatty(),
            persist_profile_name=write_browser_profile_name,
        )
        selected_profile = resolved_profile.profile
        profile_source = resolved_profile.source
        logger.info(
            "Using persistent context with profile '{}' at {} (source={})",
            selected_profile.name,
            selected_profile.path,
            profile_source,
        )
    else:
        logger.info("Using transient browser context (persistent disabled)")

    async with PlaywrightClient(
        headless=headless,
        debug_dir=debug_dir,
        stealth_enabled=config.browser.stealth,
        persistent_context_enabled=config.browser.launch_persistent_context,
        user_data_dir=selected_profile.path if selected_profile is not None else None,
    ) as client:
        try:
            runner = build_runner(config, client)
            result = await runner.run()
        except Exception as exc:
            logger.exception("Run failed")
            raise SystemExit(1) from exc

        raise SystemExit(0 if result.success else 1)


def build_runner(config, client: PlaywrightClient) -> GrabRunner:
    if client.page is None:
        raise RuntimeError("Playwright page is not initialized")

    page_api = BrowserPageApi(client.page)
    page_strategy = PageBookingStrategy(
        client.page,
        config=config,
        sleep=asyncio.sleep,
        debug_snapshot=client.capture_snapshot,
    )

    def sync_active_page(page) -> None:
        client.page = page
        page_api.page = page
        page_strategy.page = page

    auth_service = AuthService(client.page, config, notify=logger.info)
    session_service = SessionCaptureService(
        client.page,
        config,
        debug_snapshot=client.capture_snapshot,
        debug_state_provider=client.collect_debug_state,
        on_page_change=sync_active_page,
    )
    scheduler = Scheduler(config)
    schedule_service = ScheduleService(page_api, config=config, sleep=asyncio.sleep)
    booking_service = BookingService(page_strategy=page_strategy)
    return GrabRunner(
        auth_service,
        session_service,
        scheduler,
        schedule_service,
        booking_service,
    )


async def run_create_profile_flow(
    *,
    config: GrabConfig,
    requested_profile_name: str | None,
    debug_dir: Path | None,
) -> None:
    profile = create_profile(
        config.browser.profiles_root_dir,
        profile_name=requested_profile_name,
    )
    print(f"✅ 已创建 profile: {profile.name}")
    print(f"   路径: {profile.path}")
    print(
        "ℹ️ 将打开一个持久化浏览器用于暖机。"
        "你可以自行访问 160、登录 160，或做少量普通浏览。"
    )
    print("   关闭浏览器窗口后，此命令会自动结束。")

    async with PlaywrightClient(
        headless=False,
        debug_dir=debug_dir,
        stealth_enabled=config.browser.stealth,
        persistent_context_enabled=True,
        user_data_dir=profile.path,
    ) as client:
        if client.page is not None:
            await client.page.goto("about:blank")
        if client.context is None:
            raise RuntimeError("Persistent browser context is not initialized")
        await client.context.wait_for_event("close")


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
