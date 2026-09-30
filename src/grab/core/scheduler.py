import asyncio
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from grab.models.schemas import GrabConfig
from grab.observability.safe_logging import logger
from grab.utils.schedule_time import schedule_instant


class Scheduler:
    def __init__(
        self,
        config: GrabConfig,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        reporter=None,
        is_interactive: bool | None = None,
        prompt_text: Callable[[str], str] = input,
    ):
        self.config = config
        self._now = now or (lambda: datetime.now(UTC))
        self._sleep = sleep or asyncio.sleep
        self.reporter = reporter
        self.is_interactive = (
            sys.stdin.isatty() if is_interactive is None else is_interactive
        )
        self._prompt_text = prompt_text

    def _remaining(self) -> float:
        return (
            self.config.appoint_time
            - schedule_instant(self._now(), self.config.schedule.timezone)
        ).total_seconds()

    async def wait_until_ready(self) -> bool:
        if not self.config.enable_appoint or self.config.appoint_time is None:
            return True

        if self.reporter is not None:
            await self.reporter.emit_event(
                "scheduler_wait_started",
                level="info",
                message="Waiting until appoint time before starting schedule polling.",
                data={"remaining_seconds": max(0, int(self._remaining()))},
            )

        while (remaining := self._remaining()) > 0:
            logger.info("Diagnostic event.")
            # asyncio.sleep uses a monotonic timer. Retain fractions to avoid a
            # zero-delay busy loop during the last second; cancellation propagates.
            await self._sleep(min(5.0, remaining))

        if -remaining <= self.config.schedule.late_start_grace_seconds:
            return True
        logger.warning("Scheduled start missed; manual confirmation required.")
        if not self.is_interactive:
            return False
        return self._prompt_text(
            "已超过定时启动宽限期，是否现在开始轮询？[y/N] "
        ).strip().lower() in {"y", "yes"}
