"""Query helpers for `shipment_proofs`."""

from __future__ import annotations

from bot.database.core import Database


async def create_proof(
    db: Database,
    *,
    guild_id: int,
    channel_id: int,
    staff_user_id: int,
    customer_user_id: int,
    category: str,
    product: str,
    shipped_date: str,
    status: str,
    photo_url: str,
) -> int:
    return await db.execute(
        """
        INSERT INTO shipment_proofs
            (guild_id, channel_id, staff_user_id, customer_user_id, category,
             product, shipped_date, status, photo_url)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            guild_id, channel_id, staff_user_id, customer_user_id, category,
            product, shipped_date, status, photo_url,
        ),
    )


async def set_message_id(db: Database, proof_id: int, message_id: int) -> None:
    await db.execute("UPDATE shipment_proofs SET message_id = ? WHERE id = ?", (message_id, proof_id))


async def list_recent(db: Database, guild_id: int, limit: int = 10):
    return await db.fetchall(
        "SELECT * FROM shipment_proofs WHERE guild_id = ? ORDER BY created_at DESC LIMIT ?",
        (guild_id, limit),
    )
