import argparse
import asyncio
import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from loguru import logger

from grab.browser.page_api import BrowserPageApi
from grab.browser.playwright_client import PlaywrightClient
from grab.core.runner import GrabRunner
from grab.core.scheduler import Scheduler
from grab.models.schemas import GrabConfig
from grab.observability import build_run_reporter
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

APP_NAME = "160Grab"
CONFIG_FILENAME = "config.yaml"
CONFIG_TEMPLATE_PATH = Path("config") / "example.yaml"


def setup_logging(verbose: bool = False) -> None:
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=level)


def _optional_path_env(name: str) -> Path | None:
    value = os.getenv(name)
    if not value:
        return None
    return Path(value).expanduser()


def is_frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def get_executable_dir(executable: str | Path | None = None) -> Path:
    target = Path(executable or sys.executable)
    return target.resolve().parent


def get_resource_root() -> Path:
    if is_frozen_app():
        return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return Path(__file__).resolve().parent


def get_template_config_path() -> Path:
    return get_resource_root() / CONFIG_TEMPLATE_PATH


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="160Grab CLI")
    parser.add_argument("config_path", nargs="?")
    parser.add_argument(
        "--create-profile",
        action="store_true",
        help="Create a new persistent browser profile and open it for warm-up.",
    )
    parser.add_argument(
        "--profile-name",
        help="Explicit profile name for --create-profile.",
    )
    parser.add_argument(
        "--smoke-browser",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args(argv)
    if args.profile_name and not args.create_profile:
        parser.error("--profile-name can only be used together with --create-profile")
    return args


def resolve_config_path(
    args: argparse.Namespace,
    *,
    frozen: bool | None = None,
    executable: str | Path | None = None,
) -> tuple[Path, bool]:
    if args.config_path is not None:
        return Path(args.config_path).expanduser(), True

    frozen_app = is_frozen_app() if frozen is None else frozen
    if frozen_app:
        return get_executable_dir(executable) / CONFIG_FILENAME, False

    return Path(CONFIG_FILENAME).expanduser(), False


def ensure_frozen_default_config(
    config_path: Path,
    *,
    template_path: Path | None = None,
    output: Callable[[str], None] = print,
) -> bool:
    if config_path.exists():
        return False

    template = template_path or get_template_config_path()
    if not template.exists():
        raise FileNotFoundError(f"Config template was not found: {template}")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, config_path)
    output(f"未找到 {CONFIG_FILENAME}，已在 {config_path} 生成配置模板。")
    output("请先按需修改配置后重新运行。")
    return True


async def run_smoke_browser(*, debug_dir: Path | None) -> None:
    async with PlaywrightClient(
        headless=True,
        debug_dir=debug_dir,
        stealth_enabled=True,
        persistent_context_enabled=False,
    ) as client:
        await client.goto("about:blank")


async def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    debug_dir = _optional_path_env("GRAB_DEBUG_DIR")
    logger.info("{} - 健康160自动挂号", APP_NAME)
    if args.smoke_browser:
        await run_smoke_browser(debug_dir=debug_dir)
        raise SystemExit(0)

    config_path, config_path_explicit = resolve_config_path(args)
    if is_frozen_app() and not config_path_explicit:
        if ensure_frozen_default_config(config_path):
            raise SystemExit(0)

    config = load_config(config_path)
    headless = config.auth.strategy != "manual"
    if args.create_profile:
        await run_create_profile_flow(
            config=config,
            requested_profile_name=args.profile_name,
            debug_dir=debug_dir,
        )
        raise SystemExit(0)

    reporter = build_run_reporter(config)
    if reporter.jsonl_path is not None:
        logger.info("Structured run events will be written to {}", reporter.jsonl_path)
    else:
        logger.warning(
            "Structured run events are disabled because the JSONL sink could not be initialized"
        )
    await reporter.emit_event(
        "run_started",
        level="info",
        message="Run started.",
        data={
            "config_path": str(config_path),
            "debug_dir": str(debug_dir) if debug_dir is not None else None,
            "desktop_notifications": config.notifications.desktop,
            "webhook_enabled": bool(config.notifications.webhook.url),
            "jsonl_path": str(reporter.jsonl_path),
        },
    )

    runner_started = False
    try:
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
            runner = build_runner(config, client, reporter=reporter)
            runner_started = True
            result = await runner.run()
    except Exception as exc:
        if not runner_started:
            await reporter.emit_event(
                "run_failed",
                level="error",
                message=f"Run failed during phase {reporter.current_phase}: {exc}",
                data={
                    "phase": reporter.current_phase,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                notify=True,
                notification_title="160Grab 运行失败",
                notification_severity="error",
            )
        await reporter.emit_event(
            "run_finished",
            level="info",
            message="Run finished with failure.",
            data={
                "success": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        logger.exception("Run failed")
        raise SystemExit(1) from exc

    await reporter.emit_event(
        "run_finished",
        level="info" if result.success else "warning",
        message=(
            "Run finished successfully."
            if result.success
            else "Run finished without a successful booking."
        ),
        data={
            "success": result.success,
            "booked_slot_id": result.booked_slot_id,
        },
    )
    raise SystemExit(0 if result.success else 1)


def build_runner(config, client: PlaywrightClient, reporter=None) -> GrabRunner:
    if client.page is None:
        raise RuntimeError("Playwright page is not initialized")

    page_api = BrowserPageApi(client.page)

    async def capture_snapshot(label: str):
        path = await client.capture_snapshot(label)
        if reporter is not None and path is not None:
            await reporter.record_snapshot(label=label, path=path)
        return path

    page_strategy = PageBookingStrategy(
        client.page,
        config=config,
        sleep=asyncio.sleep,
        debug_snapshot=capture_snapshot,
        reporter=reporter,
    )

    def sync_active_page(page) -> None:
        client.page = page
        page_api.page = page
        page_strategy.page = page

    auth_service = AuthService(
        client.page,
        config,
        notify=logger.info,
        reporter=reporter,
    )
    session_service = SessionCaptureService(
        client.page,
        config,
        debug_snapshot=capture_snapshot,
        debug_state_provider=client.collect_debug_state,
        on_page_change=sync_active_page,
        reporter=reporter,
    )
    scheduler = Scheduler(config, reporter=reporter)
    schedule_service = ScheduleService(
        page_api,
        config=config,
        sleep=asyncio.sleep,
        reporter=reporter,
    )
    booking_service = BookingService(page_strategy=page_strategy)
    return GrabRunner(
        auth_service,
        session_service,
        scheduler,
        schedule_service,
        booking_service,
        reporter=reporter,
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
