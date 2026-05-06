import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

from loguru import logger

from grab.models.schemas import GrabConfig


class Scheduler:
    def __init__(
        self,
        config: GrabConfig,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[int], Awaitable[None]] | None = None,
        reporter=None,
    ):
        self.config = config
        self._now = now or datetime.now
        self._sleep = sleep or asyncio.sleep
        self.reporter = reporter

    async def wait_until_ready(self) -> None:
        if not self.config.enable_appoint or self.config.appoint_time is None:
            return

        if self.reporter is not None:
            remaining_seconds = max(
                0,
                int((self.config.appoint_time - self._now()).total_seconds()),
            )
            await self.reporter.emit_event(
                "scheduler_wait_started",
                level="info",
                message="Waiting until appoint time before starting schedule polling.",
                data={
                    "appoint_time": self.config.appoint_time,
                    "remaining_seconds": remaining_seconds,
                },
            )

        while self._now() < self.config.appoint_time:
            remaining = int((self.config.appoint_time - self._now()).total_seconds())
            delay = min(5, remaining)
            logger.info(f"Waiting {delay}s until appoint time")
            await self._sleep(delay)
