"""Query helpers buat channel voice AFK -- disimpen di tabel `settings`
(key/value) yang udah ada, jadi gak butuh tabel/migrasi baru. Satu key per
server: `voice_afk_channel:<guild_id>` -> ID voice channel tujuan. Lihat
bot.cogs.voice buat yang manggil ini."""

from __future__ import annotations

from bot.database.core import Database

KEY_PREFIX = "voice_afk_channel:"


def _key(guild_id: int) -> str:
    return f"{KEY_PREFIX}{guild_id}"


async def set_voice_channel(db: Database, guild_id: int, channel_id: int) -> None:
    await db.execute(
        """
        INSERT INTO settings (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (_key(guild_id), str(channel_id)),
    )


async def get_voice_channel(db: Database, guild_id: int) -> int | None:
    row = await db.fetchone("SELECT value FROM settings WHERE key = ?", (_key(guild_id),))
    if not row or not row["value"] or not str(row["value"]).isdigit():
        return None
    return int(row["value"])


async def clear_voice_channel(db: Database, guild_id: int) -> None:
    await db.execute("DELETE FROM settings WHERE key = ?", (_key(guild_id),))


async def list_voice_channels(db: Database) -> list[tuple[int, int]]:
    """Semua (guild_id, channel_id) yang tersimpen -- dipake buat auto-rejoin
    abis restart. Row dengan format rusak di-skip, bukan bikin error."""
    rows = await db.fetchall("SELECT key, value FROM settings WHERE key LIKE ?", (f"{KEY_PREFIX}%",))
    result: list[tuple[int, int]] = []
    for row in rows:
        guild_part = row["key"][len(KEY_PREFIX):]
        if guild_part.isdigit() and row["value"] and str(row["value"]).isdigit():
            result.append((int(guild_part), int(row["value"])))
    return result
