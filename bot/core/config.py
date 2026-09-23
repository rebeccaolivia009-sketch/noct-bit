"""
Centralised environment configuration for NOCTRA.

All runtime configuration is sourced from environment variables (.env locally,
Railway variables in production). Nothing here talks to Discord or the
database directly -- this module only knows how to read and validate config.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str, default: int | None = None) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_int_list(name: str) -> list[int]:
    raw = os.getenv(name)
    if not raw:
        return []
    result = []
    for piece in raw.split(","):
        piece = piece.strip()
        if piece.isdigit():
            result.append(int(piece))
    return result


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Config:
    # Core bot identity
    token: str = field(default_factory=lambda: os.getenv("DISCORD_TOKEN", ""))
    guild_id: int | None = field(default_factory=lambda: _get_int("GUILD_ID"))

    # One-time cleanup: if a server has duplicate/stale slash commands left
    # over from an earlier GUILD_ID-based sync, list its ID(s) here
    # (comma-separated for more than one) to wipe the guild-specific
    # registrations on the next startup. Safe to leave this set permanently
    # -- once cleared there's nothing left to clear, it's just a no-op on
    # every subsequent boot -- but it's fine to remove it again too.
    clear_guild_commands_for: list[int] = field(
        default_factory=lambda: _get_int_list("CLEAR_GUILD_COMMANDS_FOR")
    )

    # One-time cleanup: kalau ada command GLOBAL basi (misal bot sempet
    # dijalanin TANPA GUILD_ID di suatu titik di masa lalu, jadi ke-daftar
    # global -- ke SEMUA server bot ini join -- bukan cuma satu server).
    # Beda scope dari CLEAR_GUILD_COMMANDS_FOR di atas, jadi command global
    # yang nyangkut itu GAK kesentuh sama clear yang itu. Set
    # CLEAR_GLOBAL_COMMANDS=1 buat wipe-nya sekali. Propagasi command
    # global itu LAMBAT di sisi Discord (bisa sampe ~1 jam), beda dari
    # command guild yang biasanya instan -- jangan panik kalau hasilnya
    # gak langsung keliatan abis redeploy.
    clear_global_commands: bool = field(
        default_factory=lambda: _get_bool("CLEAR_GLOBAL_COMMANDS")
    )

    # Persistence
    database_path: str = field(
        default_factory=lambda: os.getenv("DATABASE_PATH", "data/noctra.db")
    )

    # Logging
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))

    # Access control / operational defaults (can be overridden at runtime via
    # /settings, which persists to the `settings` table and takes priority).
    staff_role_id: int | None = field(default_factory=lambda: _get_int("STAFF_ROLE_ID"))
    ticket_category_id: int | None = field(
        default_factory=lambda: _get_int("TICKET_CATEGORY_ID")
    )
    ticket_archive_category_id: int | None = field(
        default_factory=lambda: _get_int("TICKET_ARCHIVE_CATEGORY_ID")
    )
    ticket_log_channel_id: int | None = field(
        default_factory=lambda: _get_int("TICKET_LOG_CHANNEL_ID")
    )
    ticket_auto_archive_hours: int = field(
        default_factory=lambda: _get_int("TICKET_AUTO_ARCHIVE_HOURS", 24) or 24
    )

    default_currency: str = field(
        default_factory=lambda: os.getenv("DEFAULT_CURRENCY", "USD")
    )

    brand_name: str = "NOCTRA"

    def validate(self) -> list[str]:
        """Return a list of human-readable problems with the current config."""
        problems: list[str] = []
        if not self.token:
            problems.append("DISCORD_TOKEN is not set.")
        return problems


config = Config()
