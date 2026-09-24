"""Query buat state panel toko (/settings shop_panel) -- dipake
bot.cogs.shop_panel_task buat rebuild & edit footer "Terakhir update"
tiap 10 detik. Lihat schema.sql buat kenapa SATU baris per guild."""

from __future__ import annotations

from bot.database.core import Database


async def set_state(
    db: Database, guild_id: int, channel_id: int, message_id: int,
    title: str, description: str, banner_url: str | None, thumbnail_url: str | None,
    button_label: str, button_emoji: str | None,
) -> None:
    await db.execute(
        """
        INSERT INTO shop_panel_state
            (guild_id, channel_id, message_id, title, description, banner_url,
             thumbnail_url, button_label, button_emoji, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(guild_id) DO UPDATE SET
            channel_id = excluded.channel_id,
            message_id = excluded.message_id,
            title = excluded.title,
            description = excluded.description,
            banner_url = excluded.banner_url,
            thumbnail_url = excluded.thumbnail_url,
            button_label = excluded.button_label,
            button_emoji = excluded.button_emoji,
            updated_at = datetime('now')
        """,
        (
            guild_id, channel_id, message_id, title, description,
            banner_url, thumbnail_url, button_label, button_emoji,
        ),
    )


async def get_state(db: Database, guild_id: int):
    return await db.fetchone("SELECT * FROM shop_panel_state WHERE guild_id = ?", (guild_id,))


async def list_all(db: Database):
    """Dipake loop refresh (bot.cogs.shop_panel_task) -- jalan buat
    SEMUA guild yang punya panel toko aktif, bukan cuma satu."""
    return await db.fetchall("SELECT * FROM shop_panel_state")
