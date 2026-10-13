"""Query helpers for `cart_items` -- keranjang belanja per user."""

from __future__ import annotations

from bot.database.core import Database


async def list_cart(db: Database, user_id: int):
    """Isi keranjang urut dari yang pertama dimasukin."""
    return await db.fetchall(
        "SELECT * FROM cart_items WHERE user_id = ? ORDER BY rowid ASC", (user_id,)
    )


async def get_line(db: Database, user_id: int, product_id: int):
    return await db.fetchone(
        "SELECT * FROM cart_items WHERE user_id = ? AND product_id = ?", (user_id, product_id)
    )


async def count_lines(db: Database, user_id: int) -> int:
    row = await db.fetchone("SELECT COUNT(*) AS n FROM cart_items WHERE user_id = ?", (user_id,))
    return int(row["n"]) if row else 0


async def set_quantity(db: Database, user_id: int, product_id: int, quantity: int) -> None:
    """Set jumlah mutlak. quantity <= 0 = hapus barisnya."""
    if quantity <= 0:
        await remove(db, user_id, product_id)
        return
    await db.execute(
        """
        INSERT INTO cart_items (user_id, product_id, quantity) VALUES (?, ?, ?)
        ON CONFLICT(user_id, product_id) DO UPDATE SET
            quantity = excluded.quantity, updated_at = datetime('now')
        """,
        (user_id, product_id, quantity),
    )


async def remove(db: Database, user_id: int, product_id: int) -> None:
    await db.execute(
        "DELETE FROM cart_items WHERE user_id = ? AND product_id = ?", (user_id, product_id)
    )


async def remove_many(db: Database, user_id: int, product_ids: list[int]) -> None:
    if not product_ids:
        return
    placeholders = ",".join("?" for _ in product_ids)
    await db.execute(
        f"DELETE FROM cart_items WHERE user_id = ? AND product_id IN ({placeholders})",
        (user_id, *product_ids),
    )


async def clear(db: Database, user_id: int) -> None:
    await db.execute("DELETE FROM cart_items WHERE user_id = ?", (user_id,))
