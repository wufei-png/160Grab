import asyncio
import sys

from loguru import logger

from grab.browser.playwright_client import PlaywrightClient
from grab.core.scheduler import Scheduler
from grab.models.schemas import GrabConfig


def setup_logging(verbose: bool = False) -> None:
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    logger.add(sys.stderr, level=level)


async def main() -> None:
    logger.info("160Grab - 健康160自动挂号")
    async with PlaywrightClient(headless=True) as client:
        await client.goto("https://www.91160.com/")
        await client.screenshot("screenshot.png")
    logger.info("Done")


if __name__ == "__main__":
    setup_logging()
    asyncio.run(main())
