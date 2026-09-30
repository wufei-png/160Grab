import argparse
import asyncio
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError

from grab.browser.page_api import BrowserPageApi
from grab.browser.playwright_client import PlaywrightClient
from grab.core.runner import GrabRunner
from grab.core.scheduler import Scheduler
from grab.models.schemas import GrabConfig
from grab.observability import build_run_reporter
from grab.observability.safe_logging import logger
from grab.services.auth import AuthService
from grab.services.booking import BookingService, PageBookingStrategy
from grab.services.schedule import ScheduleService
from grab.services.session import SessionCaptureService
from grab.transactions.store import AttemptStore, StoreBlocked
from grab.utils.config_loader import load_config
from grab.utils.config_writer import write_browser_profile_name
from grab.utils.private_files import private_directory
from grab.utils.profile_manager import (
    BrowserProfile,
    create_profile,
    resolve_profile_for_run,
)
from grab.utils.retention import cleanup_outputs

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


def emit_console_message(
    message: str,
    *,
    stream: TextIO | None = None,
    end: str = "\n",
) -> None:
    target = stream or sys.stdout
    payload = f"{message}{end}"
    try:
        target.write(payload)
    except UnicodeEncodeError:
        encoding = getattr(target, "encoding", None) or "utf-8"
        safe_payload = payload.encode(encoding, errors="backslashreplace").decode(
            encoding
        )
        target.write(safe_payload)
    flush = getattr(target, "flush", None)
    if callable(flush):
        flush()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="160Grab CLI")
    parser.add_argument("config_path", nargs="?")
    parser.add_argument("--pending-attempts", action="store_true")
    parser.add_argument("--resolve-attempt", metavar="ATTEMPT_ID")
    parser.add_argument("--resolution", choices=["booked", "not-booked"])
    parser.add_argument("--revoke-consent", action="store_true")
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
    parser.add_argument(
        "--cleanup-data",
        action="store_true",
        help="Clean expired application logs/debug output without launching a browser.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview cleanup counts without deleting files.",
    )
    args = parser.parse_args(argv)
    if bool(args.resolve_attempt) != bool(args.resolution):
        parser.error("--resolve-attempt and --resolution are required together")
    if args.dry_run and not args.cleanup_data:
        parser.error("--dry-run requires --cleanup-data")
    if args.cleanup_data and (args.create_profile or args.smoke_browser):
        parser.error("--cleanup-data cannot launch a browser")
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
    output: Callable[[str], None] | None = None,
) -> bool:
    if config_path.exists():
        return False

    template = template_path or get_template_config_path()
    if not template.exists():
        raise FileNotFoundError(f"Config template was not found: {template}")

    with private_directory(config_path.parent) as directory:
        directory.create(config_path.name, template.read_bytes())
    writer = output or emit_console_message
    writer(f"未找到 {CONFIG_FILENAME}，已在 {config_path} 生成配置模板。")
    writer("请先按需修改配置后重新运行。")
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
    logger.info("160Grab started.")
    if args.smoke_browser:
        await run_smoke_browser(debug_dir=debug_dir)
        raise SystemExit(0)

    if args.pending_attempts or args.resolve_attempt or args.revoke_consent:
        store = AttemptStore()
        try:
            if args.resolve_attempt:
                if (
                    not sys.stdin.isatty()
                    or input("已在原站核对预约记录？输入 VERIFIED 确认：") != "VERIFIED"
                ):
                    raise SystemExit(2)
                store.resolve(args.resolve_attempt, booked=args.resolution == "booked")
            if args.revoke_consent:
                store.revoke()
            for record in store.pending():
                emit_console_message(
                    f"{record['attempt_id']}: OUTCOME_UNKNOWN — 请在原站核对预约记录"
                )
        except StoreBlocked:
            emit_console_message("提交记录不可用；停止自动操作，请人工核对原站记录。")
            raise SystemExit(3) from None
        raise SystemExit(0)

    config_path, config_path_explicit = resolve_config_path(args)
    if is_frozen_app() and not config_path_explicit:
        if ensure_frozen_default_config(config_path):
            raise SystemExit(0)

    try:
        config = load_config(config_path)
    except ValidationError:
        # Pydantic's default error rendering includes rejected input values.
        logger.error("Invalid configuration; check the configured fields and types.")
        raise SystemExit(1) from None
    if args.cleanup_data:
        roots = [(config.logging.jsonl_dir, "logs")]
        if debug_dir is not None:
            roots.append((debug_dir, "debug"))
        for root, kind in roots:
            try:
                result = cleanup_outputs(
                    root,
                    kind=kind,
                    dry_run=args.dry_run,
                    protected_paths=(config.browser.profiles_root_dir,),
                )
            except OSError:
                logger.error("Run failed")
                raise SystemExit(1) from None
            emit_console_message(
                f"{kind}: eligible={result['eligible']}, deleted={result['deleted']}, skipped={result['skipped']}, dry_run={args.dry_run}"
            )
        raise SystemExit(0)
    headless = False
    if args.create_profile:
        await run_create_profile_flow(
            config=config,
            requested_profile_name=args.profile_name,
            debug_dir=debug_dir,
        )
        raise SystemExit(0)

    reporter = build_run_reporter(config)
    if reporter.jsonl_path is not None:
        logger.info("Structured run event output enabled.")
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
        if config.browser.launch_persistent_context:
            resolved_profile = resolve_profile_for_run(
                root_dir=config.browser.profiles_root_dir,
                configured_profile_name=config.browser.profile_name,
                config_path=config_path,
                prompt_text=input,
                notify=emit_console_message,
                is_interactive=sys.stdin.isatty(),
                persist_profile_name=write_browser_profile_name,
            )
            selected_profile = resolved_profile.profile
            logger.info("Using persistent browser profile.")
        else:
            logger.info("Using transient browser context (persistent disabled)")

        async with PlaywrightClient(
            headless=headless,
            debug_dir=debug_dir,
            include_sensitive_debug=config.logging.include_sensitive_debug,
            stealth_enabled=config.browser.stealth,
            persistent_context_enabled=config.browser.launch_persistent_context,
            user_data_dir=selected_profile.path
            if selected_profile is not None
            else None,
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
        logger.error("Run failed")
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
            "state": result.state,
        },
    )
    raise SystemExit(result.exit_code)


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
        page_api=page_api,
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
        session_refresh=session_service.refresh_session_for_polling,
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
    emit_console_message(f"✅ 已创建 profile: {profile.name}")
    emit_console_message(f"   路径: {profile.path}")
    emit_console_message(
        "ℹ️ 将打开一个持久化浏览器用于暖机。"
        "你可以自行访问 160、登录 160，或做少量普通浏览。"
    )
    emit_console_message("   关闭浏览器窗口后，此命令会自动结束。")

    async with PlaywrightClient(
        headless=False,
        debug_dir=debug_dir,
        include_sensitive_debug=config.logging.include_sensitive_debug,
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
    try:
        asyncio.run(main())
    except Exception:
        logger.error("Run failed")
        raise SystemExit(1) from None
