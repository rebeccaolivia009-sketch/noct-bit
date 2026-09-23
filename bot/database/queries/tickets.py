"""Query helpers for the `tickets` table."""

from __future__ import annotations

from bot.database.core import Database


async def create_ticket(
    db: Database, user_id: int, channel_id: int, kind: str, order_id: int | None = None
) -> int:
    return await db.execute(
        "INSERT INTO tickets (order_id, user_id, channel_id, kind) VALUES (?, ?, ?, ?)",
        (order_id, user_id, channel_id, kind),
    )


async def get_ticket_by_channel(db: Database, channel_id: int):
    return await db.fetchone("SELECT * FROM tickets WHERE channel_id = ?", (channel_id,))


async def get_ticket(db: Database, ticket_id: int):
    return await db.fetchone("SELECT * FROM tickets WHERE id = ?", (ticket_id,))


async def set_ticket_status(
    db: Database, channel_id: int, status: str, close_reason: str | None = None
) -> None:
    if status == "closed" or status == "archived":
        await db.execute(
            """
            UPDATE tickets SET status = ?, close_reason = ?, closed_at = datetime('now')
            WHERE channel_id = ?
            """,
            (status, close_reason, channel_id),
        )
    else:
        await db.execute(
            "UPDATE tickets SET status = ?, close_reason = NULL, closed_at = NULL WHERE channel_id = ?",
            (status, channel_id),
        )


async def set_ticket_claim(db: Database, channel_id: int, user_id: int | None) -> None:
    """Set (or clear, when user_id is None) who is currently handling this
    ticket -- powers the Claim/Unclaim buttons on the ticket panel."""
    await db.execute(
        "UPDATE tickets SET claimed_by = ? WHERE channel_id = ?", (user_id, channel_id)
    )


async def touch_activity(db: Database, channel_id: int) -> None:
    await db.execute(
        "UPDATE tickets SET last_activity_at = datetime('now') WHERE channel_id = ?",
        (channel_id,),
    )


async def list_open_tickets(db: Database):
    return await db.fetchall("SELECT * FROM tickets WHERE status = 'open'")


async def list_stale_tickets(db: Database, hours: int):
    return await db.fetchall(
        """
        SELECT * FROM tickets
        WHERE status = 'open'
          AND last_activity_at <= datetime('now', ?)
        """,
        (f"-{hours} hours",),
    )
