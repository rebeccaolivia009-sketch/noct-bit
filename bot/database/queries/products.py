"""Query helpers for the `products` table."""

from __future__ import annotations

from bot.database.core import Database

VALID_PRODUCT_TYPES = ("manual", "automatic", "digital", "service")
VALID_STOCK_TYPES = ("unlimited", "manual")
VALID_DISCOUNT_TYPES = (None, "percent", "flat")


async def create_product(
    db: Database,
    category_type_id: int,
    name: str,
    description: str | None,
    product_type: str,
    stock_type: str,
    stock_quantity: int,
    base_price: float,
    currency_label: str,
    image_url: str | None = None,
    emoji: str | None = None,
) -> int:
    row = await db.fetchone("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM products")
    position = row["p"] if row else 0
    return await db.execute(
        """
        INSERT INTO products
            (category_type_id, name, description, image_url, emoji, product_type, stock_type,
             stock_quantity, base_price, currency_label, position)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            category_type_id, name, description, image_url, emoji, product_type, stock_type,
            stock_quantity, base_price, currency_label, position,
        ),
    )


async def update_product(db: Database, product_id: int, **fields) -> None:
    """Generic partial update. Pass column=value kwargs for any product column."""
    if not fields:
        return
    allowed = {
        "category_type_id", "name", "description", "image_url", "emoji", "product_type",
        "stock_type", "stock_quantity", "visible", "base_price", "currency_label",
        "discount_type", "discount_value", "position",
    }
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return
    params.append(product_id)
    await db.execute(f"UPDATE products SET {', '.join(sets)} WHERE id = ?", tuple(params))


async def delete_product(db: Database, product_id: int) -> None:
    await db.execute("DELETE FROM products WHERE id = ?", (product_id,))


async def set_product_visible(db: Database, product_id: int, visible: bool) -> None:
    await db.execute("UPDATE products SET visible = ? WHERE id = ?", (int(visible), product_id))


async def get_product(db: Database, product_id: int):
    return await db.fetchone("SELECT * FROM products WHERE id = ?", (product_id,))


async def list_products(
    db: Database, category_type_id: int | None = None, visible_only: bool = False
):
    query = "SELECT * FROM products WHERE 1 = 1"
    params: list = []
    if category_type_id is not None:
        query += " AND category_type_id = ?"
        params.append(category_type_id)
    if visible_only:
        query += " AND visible = 1"
    query += " ORDER BY position ASC, id ASC"
    return await db.fetchall(query, tuple(params))


async def adjust_stock(db: Database, product_id: int, delta: int) -> None:
    """Increment/decrement manual stock. Never goes below zero."""
    await db.execute(
        "UPDATE products SET stock_quantity = MAX(0, stock_quantity + ?) WHERE id = ?",
        (delta, product_id),
    )


async def search_products(db: Database, term: str, limit: int = 25, visible_only: bool = False):
    query = "SELECT * FROM products WHERE name LIKE ?"
    params: list = [f"%{term}%"]
    if visible_only:
        query += " AND visible = 1"
    query += " ORDER BY name ASC LIMIT ?"
    params.append(limit)
    return await db.fetchall(query, tuple(params))
