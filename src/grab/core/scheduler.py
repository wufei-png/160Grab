import asyncio
from loguru import logger

from grab.models.schemas import GrabConfig, Slot


class Scheduler:
    def __init__(self, config: GrabConfig):
        self.config = config
        self.running = False

    async def start(self) -> None:
        self.running = True
        logger.info(f"Scheduler started, interval={self.config.interval_seconds}s")
        while self.running:
            try:
                logger.debug("Checking slots...")
                await asyncio.sleep(self.config.interval_seconds)
            except asyncio.CancelledError:
                break
        logger.info("Scheduler stopped")

    def stop(self) -> None:
        self.running = False
        logger.info("Scheduler stop requested")
