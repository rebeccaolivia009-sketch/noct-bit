"""Queries buat tier loyalitas (/tier) -- tiap tier = nama + role Discord +
minimal total belanja. Total belanja dihitung dari order completed+paid
(lihat bot.database.queries.buyer_stats)."""

from __future__ import annotations

from bot.database.core import Database

_SCHEMA = """
CREATE TABLE IF NOT EXISTS loyalty_tiers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER NOT NULL,
    name         TEXT NOT NULL,
    role_id      INTEGER NOT NULL,
    min_spend    REAL NOT NULL,
    description  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (guild_id, name COLLATE NOCASE)
)
"""


async def ensure_tables(db: Database) -> None:
    await db.execute(_SCHEMA)


async def list_tiers(db: Database, guild_id: int):
    """Urut dari ambang terendah ke tertinggi."""
    await ensure_tables(db)
    return await db.fetchall(
        "SELECT * FROM loyalty_tiers WHERE guild_id = ? ORDER BY min_spend ASC, id ASC", (guild_id,)
    )


async def list_guilds_with_tiers(db: Database) -> list[int]:
    await ensure_tables(db)
    rows = await db.fetchall("SELECT DISTINCT guild_id FROM loyalty_tiers")
    return [int(r["guild_id"]) for r in rows]


async def get_tier(db: Database, guild_id: int, name: str):
    await ensure_tables(db)
    return await db.fetchone(
        "SELECT * FROM loyalty_tiers WHERE guild_id = ? AND name = ? COLLATE NOCASE", (guild_id, name)
    )


async def add_tier(
    db: Database, guild_id: int, name: str, role_id: int, min_spend: float, description: str | None
) -> int | None:
    await ensure_tables(db)
    return await db.execute(
        "INSERT INTO loyalty_tiers (guild_id, name, role_id, min_spend, description) VALUES (?, ?, ?, ?, ?)",
        (guild_id, name, role_id, min_spend, description),
    )


async def update_tier(
    db: Database, tier_id: int, *, name: str | None = None, role_id: int | None = None,
    min_spend: float | None = None, description: str | None = None,
) -> None:
    """Cuma field yang diisi (bukan None) yang keubah. Deskripsi kosong ("")
    artinya dihapus."""
    fields = {"name": name, "role_id": role_id, "min_spend": min_spend, "description": description}
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        return
    clause = ", ".join(f"{k} = ?" for k in fields)
    await db.execute(f"UPDATE loyalty_tiers SET {clause} WHERE id = ?", (*fields.values(), tier_id))


async def remove_tier(db: Database, tier_id: int) -> None:
    await db.execute("DELETE FROM loyalty_tiers WHERE id = ?", (tier_id,))
