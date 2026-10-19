"""Query helpers for `auto_reactions` -- reaksi otomatis per channel."""

from __future__ import annotations

import json

from bot.database.core import Database


def emojis_of(row) -> list[str]:
    """Daftar emoji dari satu baris (JSON -> list). Rusak/kosong -> []."""
    try:
        value = json.loads(row["emojis"])
    except (TypeError, ValueError):
        return []
    return [str(e) for e in value] if isinstance(value, list) else []


async def get_by_channel(db: Database, channel_id: int):
    return await db.fetchone("SELECT * FROM auto_reactions WHERE channel_id = ?", (channel_id,))


async def list_guild(db: Database, guild_id: int):
    return await db.fetchall("SELECT * FROM auto_reactions WHERE guild_id = ? ORDER BY id ASC", (guild_id,))


async def list_enabled(db: Database):
    """Semua konfigurasi aktif (lintas server) -- di-cache cog biar listener
    on_message gak query DB buat tiap pesan."""
    return await db.fetchall("SELECT * FROM auto_reactions WHERE enabled = 1")


async def upsert(
    db: Database,
    guild_id: int,
    channel_id: int,
    *,
    emojis: list[str],
    filter_mode: str,
    ignore_bots: bool,
    enabled: bool,
    created_by: int | None = None,
) -> None:
    await db.execute(
        """
        INSERT INTO auto_reactions (guild_id, channel_id, emojis, filter_mode, ignore_bots, enabled, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(channel_id) DO UPDATE SET
            guild_id = excluded.guild_id,
            emojis = excluded.emojis,
            filter_mode = excluded.filter_mode,
            ignore_bots = excluded.ignore_bots,
            enabled = excluded.enabled,
            updated_at = datetime('now')
        """,
        (guild_id, channel_id, json.dumps(emojis, ensure_ascii=False), filter_mode, int(ignore_bots), int(enabled), created_by),
    )


async def delete(db: Database, channel_id: int) -> None:
    await db.execute("DELETE FROM auto_reactions WHERE channel_id = ?", (channel_id,))
