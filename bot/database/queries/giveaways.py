"""Query helpers for `giveaways`, `giveaway_entries`, and `giveaway_winners`."""

from __future__ import annotations

from bot.database.core import Database


async def create_giveaway(
    db: Database,
    *,
    guild_id: int,
    channel_id: int,
    host_user_id: int,
    title: str,
    description: str | None,
    prize: str,
    winner_count: int,
    win_role_id: int | None,
    button_label: str,
    button_style: str,
    button_emoji: str | None,
    color: int | None,
    ends_at: str,
) -> int:
    return await db.execute(
        """
        INSERT INTO giveaways
            (guild_id, channel_id, host_user_id, title, description, prize,
             winner_count, win_role_id, button_label, button_style,
             button_emoji, color, ends_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id, channel_id, host_user_id, title, description, prize,
            winner_count, win_role_id, button_label, button_style,
            button_emoji, color, ends_at,
        ),
    )


async def set_message_id(db: Database, giveaway_id: int, message_id: int) -> None:
    await db.execute("UPDATE giveaways SET message_id = ? WHERE id = ?", (message_id, giveaway_id))


async def get_giveaway(db: Database, giveaway_id: int):
    return await db.fetchone("SELECT * FROM giveaways WHERE id = ?", (giveaway_id,))


async def get_giveaway_by_message(db: Database, message_id: int):
    return await db.fetchone("SELECT * FROM giveaways WHERE message_id = ?", (message_id,))


async def list_active_giveaways(db: Database, guild_id: int | None = None):
    if guild_id is not None:
        return await db.fetchall(
            "SELECT * FROM giveaways WHERE status = 'active' AND guild_id = ? ORDER BY ends_at ASC",
            (guild_id,),
        )
    return await db.fetchall("SELECT * FROM giveaways WHERE status = 'active' ORDER BY ends_at ASC")


async def list_expired_active_giveaways(db: Database):
    """Giveaway yang statusnya masih 'active' tapi ends_at-nya udah lewat --
    dipake background task buat auto-end (lihat bot.cogs.tasks)."""
    return await db.fetchall(
        "SELECT * FROM giveaways WHERE status = 'active' AND ends_at <= datetime('now')"
    )


async def set_status(db: Database, giveaway_id: int, status: str) -> None:
    await db.execute(
        "UPDATE giveaways SET status = ?, ended_at = datetime('now') WHERE id = ?",
        (status, giveaway_id),
    )


async def add_entry(db: Database, giveaway_id: int, user_id: int) -> None:
    await db.execute(
        "INSERT OR IGNORE INTO giveaway_entries (giveaway_id, user_id) VALUES (?, ?)",
        (giveaway_id, user_id),
    )


async def remove_entry(db: Database, giveaway_id: int, user_id: int) -> None:
    await db.execute(
        "DELETE FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?",
        (giveaway_id, user_id),
    )


async def has_entered(db: Database, giveaway_id: int, user_id: int) -> bool:
    row = await db.fetchone(
        "SELECT 1 FROM giveaway_entries WHERE giveaway_id = ? AND user_id = ?",
        (giveaway_id, user_id),
    )
    return row is not None


async def count_entries(db: Database, giveaway_id: int) -> int:
    row = await db.fetchone(
        "SELECT COUNT(*) AS c FROM giveaway_entries WHERE giveaway_id = ?", (giveaway_id,)
    )
    return row["c"] if row else 0


async def list_entrant_ids(db: Database, giveaway_id: int) -> list[int]:
    rows = await db.fetchall(
        "SELECT user_id FROM giveaway_entries WHERE giveaway_id = ?", (giveaway_id,)
    )
    return [r["user_id"] for r in rows]


async def record_winners(db: Database, giveaway_id: int, user_ids: list[int]) -> None:
    if not user_ids:
        return
    await db.executemany(
        "INSERT INTO giveaway_winners (giveaway_id, user_id) VALUES (?, ?)",
        [(giveaway_id, uid) for uid in user_ids],
    )


async def list_winners(db: Database, giveaway_id: int) -> list[int]:
    rows = await db.fetchall(
        "SELECT user_id FROM giveaway_winners WHERE giveaway_id = ? ORDER BY id ASC",
        (giveaway_id,),
    )
    return [r["user_id"] for r in rows]


async def clear_winners(db: Database, giveaway_id: int) -> None:
    await db.execute("DELETE FROM giveaway_winners WHERE giveaway_id = ?", (giveaway_id,))
