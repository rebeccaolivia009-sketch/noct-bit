"""NOCTRA -- entrypoint. Run with `python main.py`."""

from __future__ import annotations

import asyncio
import sys

from bot.core.bot import NoctraBot
from bot.core.config import config
from bot.core.logger import setup_logging


async def main() -> None:
    logger = setup_logging()

    problems = config.validate()
    if problems:
        for problem in problems:
            logger.error(problem)
        sys.exit(1)

    bot = NoctraBot()
    try:
        await bot.start(config.token)
    finally:
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
