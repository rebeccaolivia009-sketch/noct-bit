"""
Query helpers for the `settings` key/value table.

Runtime settings configured via `/settings` (staff role, ticket category,
log channel, auto-archive hours, default currency...) live here and take
priority over the `.env` defaults baked into `bot/core/config.py`. This lets
admins reconfigure the bot without redeploying.
"""

from __future__ import annotations

from bot.database.core import Database

KNOWN_KEYS = (
    "staff_role_id",
    "order_log_channel_id",
    "reviews_channel_id",
    "brand_logo_url",
    "leaderboard_channel_id",
    "leaderboard_message_id",
    "ticket_category_id",
    "ticket_archive_category_id",
    "ticket_log_channel_id",
    "ticket_auto_archive_hours",
    "default_currency",
    "shop_panel_channel_id",
    "ticket_panel_channel_id",
)


async def set_setting(db: Database, key: str, value: str) -> None:
    await db.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


async def get_setting(db: Database, key: str) -> str | None:
    row = await db.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row else None


async def get_all_settings(db: Database) -> dict[str, str]:
    rows = await db.fetchall("SELECT key, value FROM settings")
    return {row["key"]: row["value"] for row in rows}
