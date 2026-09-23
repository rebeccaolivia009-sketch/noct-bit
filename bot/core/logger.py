"""
Logging configuration for NOCTRA.

Provides a single `setup_logging()` entrypoint that configures console
logging suitable for Railway's log viewer (plain text, no colour codes,
flushes immediately) plus quieting of noisy third-party loggers.
"""

from __future__ import annotations

import logging
import sys

from bot.core.config import config


def setup_logging() -> logging.Logger:
    level = getattr(logging, config.log_level.upper(), logging.INFO)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(handler)

    # Quiet down libraries that are noisy at INFO/DEBUG level.
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("discord.gateway").setLevel(logging.WARNING)
    logging.getLogger("discord.client").setLevel(logging.WARNING)

    return logging.getLogger("noctra")


logger = logging.getLogger("noctra")
