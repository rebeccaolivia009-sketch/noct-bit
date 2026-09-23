"""Query helpers for the `product_variants` table."""

from __future__ import annotations

from bot.database.core import Database


async def create_variant(
    db: Database,
    product_id: int,
    title: str,
    description: str | None,
    price: float,
) -> int:
    row = await db.fetchone(
        "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM product_variants WHERE product_id = ?",
        (product_id,),
    )
    position = row["p"] if row else 0
    return await db.execute(
        """
        INSERT INTO product_variants (product_id, title, description, price, position)
        VALUES (?, ?, ?, ?, ?)
        """,
        (product_id, title, description, price, position),
    )


async def update_variant(db: Database, variant_id: int, **fields) -> None:
    allowed = {
        "title", "description", "price", "discount_type", "discount_value",
        "available", "position",
    }
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return
    params.append(variant_id)
    await db.execute(
        f"UPDATE product_variants SET {', '.join(sets)} WHERE id = ?", tuple(params)
    )


async def delete_variant(db: Database, variant_id: int) -> None:
    await db.execute("DELETE FROM product_variants WHERE id = ?", (variant_id,))


async def get_variant(db: Database, variant_id: int):
    return await db.fetchone("SELECT * FROM product_variants WHERE id = ?", (variant_id,))


async def list_variants(db: Database, product_id: int, available_only: bool = False):
    query = "SELECT * FROM product_variants WHERE product_id = ?"
    params: list = [product_id]
    if available_only:
        query += " AND available = 1"
    query += " ORDER BY position ASC, id ASC"
    return await db.fetchall(query, tuple(params))
