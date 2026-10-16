"""Query helpers for `sticky_messages` -- pesan yang nempel di bawah channel."""

from __future__ import annotations

from bot.database.core import Database


async def get_by_channel(db: Database, channel_id: int):
    return await db.fetchone("SELECT * FROM sticky_messages WHERE channel_id = ?", (channel_id,))


async def list_guild(db: Database, guild_id: int):
    return await db.fetchall(
        "SELECT * FROM sticky_messages WHERE guild_id = ? ORDER BY id ASC", (guild_id,)
    )


async def list_enabled(db: Database):
    """Semua sticky aktif (lintas server) -- dipake buat nge-cache channel
    mana yang perlu dipantau, biar listener on_message gak query DB tiap
    ada pesan."""
    return await db.fetchall("SELECT * FROM sticky_messages WHERE enabled = 1")


async def upsert(
    db: Database,
    guild_id: int,
    channel_id: int,
    *,
    title: str,
    description: str,
    footer_text: str | None,
    thumbnail_url: str | None,
    accent_color: int | None,
    enabled: bool,
    created_by: int | None = None,
) -> None:
    """Bikin atau timpa sticky di `channel_id`. message_id TIDAK disentuh
    (itu urusan cog yang lagi masang pesannya)."""
    await db.execute(
        """
        INSERT INTO sticky_messages
            (guild_id, channel_id, title, description, footer_text, thumbnail_url,
             accent_color, enabled, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            guild_id = excluded.guild_id,
            title = excluded.title,
            description = excluded.description,
            footer_text = excluded.footer_text,
            thumbnail_url = excluded.thumbnail_url,
            accent_color = excluded.accent_color,
            enabled = excluded.enabled,
            updated_at = datetime('now')
        """,
        (
            guild_id, channel_id, title, description, footer_text, thumbnail_url,
            accent_color, int(enabled), created_by,
        ),
    )


async def set_message_id(db: Database, channel_id: int, message_id: int | None) -> None:
    await db.execute(
        "UPDATE sticky_messages SET message_id = ? WHERE channel_id = ?", (message_id, channel_id)
    )


async def delete(db: Database, channel_id: int) -> None:
    await db.execute("DELETE FROM sticky_messages WHERE channel_id = ?", (channel_id,))
