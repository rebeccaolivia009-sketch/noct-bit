"""Queries buat blacklist pembeli (/blacklist) -- user yang diblokir buat
order. Tabelnya dibuat sendiri (CREATE TABLE IF NOT EXISTS) tiap kali
dipanggil, jadi gak perlu nyentuh schema.sql/core.py, dan tetep aman
walau database di-import ulang lewat /backup import (file lama yang belum
punya tabel ini otomatis dibikinin lagi)."""

from __future__ import annotations

from bot.database.core import Database

_SCHEMA = """
CREATE TABLE IF NOT EXISTS blacklist (
    user_id     INTEGER PRIMARY KEY,
    reason      TEXT NOT NULL,
    added_by    INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


async def ensure_tables(db: Database) -> None:
    await db.execute(_SCHEMA)


async def get_entry(db: Database, user_id: int):
    await ensure_tables(db)
    return await db.fetchone("SELECT * FROM blacklist WHERE user_id = ?", (user_id,))


async def add_entry(db: Database, user_id: int, reason: str, added_by: int) -> bool:
    """Return True kalau user-nya BARU masuk blacklist, False kalau udah ada
    sebelumnya (alasan & pencatatnya diperbarui)."""
    existed = await get_entry(db, user_id) is not None
    await db.execute(
        """
        INSERT INTO blacklist (user_id, reason, added_by) VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET reason = excluded.reason, added_by = excluded.added_by
        """,
        (user_id, reason, added_by),
    )
    return not existed


async def remove_entry(db: Database, user_id: int) -> bool:
    """Return True kalau ada yang beneran kehapus."""
    if await get_entry(db, user_id) is None:
        return False
    await db.execute("DELETE FROM blacklist WHERE user_id = ?", (user_id,))
    return True


async def list_entries(db: Database, limit: int, offset: int = 0):
    await ensure_tables(db)
    return await db.fetchall(
        "SELECT * FROM blacklist ORDER BY created_at DESC, user_id DESC LIMIT ? OFFSET ?", (limit, offset)
    )


async def count_entries(db: Database) -> int:
    await ensure_tables(db)
    row = await db.fetchone("SELECT COUNT(*) AS n FROM blacklist")
    return int(row["n"]) if row else 0
