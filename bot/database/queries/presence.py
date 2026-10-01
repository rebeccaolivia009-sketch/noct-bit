"""Queries buat daftar teks status "Watching ..." bot yang gonta-ganti
otomatis. Lihat bot.cogs.presence_rotator buat pemakaiannya."""

from __future__ import annotations

from bot.database.core import Database


async def list_statuses(db: Database):
    return await db.fetchall("SELECT * FROM bot_presence_statuses ORDER BY position ASC, id ASC")


async def seed_if_empty(db: Database, defaults: list[str]) -> None:
    """Isi tabel SEKALI pake daftar default -- CUMA jalan kalau tabelnya
    beneran kosong (belum pernah diisi ATAU staff udah ngehapus
    semuanya), jadi gak nimpa ulang perubahan staff tiap bot restart."""
    row = await db.fetchone("SELECT COUNT(*) AS c FROM bot_presence_statuses")
    if row and row["c"] > 0:
        return
    for i, text in enumerate(defaults):
        await db.execute(
            "INSERT INTO bot_presence_statuses (text, position) VALUES (?, ?)", (text, i)
        )


async def add_status(db: Database, text: str) -> None:
    row = await db.fetchone("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM bot_presence_statuses")
    position = row["p"] if row else 0
    await db.execute(
        "INSERT INTO bot_presence_statuses (text, position) VALUES (?, ?)", (text, position)
    )


async def remove_status_at(db: Database, zero_based_index: int) -> bool:
    """Return True kalau nomor-nya valid & beneran kehapus."""
    statuses = await list_statuses(db)
    if zero_based_index < 0 or zero_based_index >= len(statuses):
        return False
    target = statuses[zero_based_index]
    await db.execute("DELETE FROM bot_presence_statuses WHERE id = ?", (target["id"],))
    return True
