"""Query helpers for the `products` table."""

from __future__ import annotations

from bot.database.core import Database

VALID_PRODUCT_TYPES = ("manual", "automatic", "digital", "service")
VALID_STOCK_TYPES = ("unlimited", "manual")
VALID_DISCOUNT_TYPES = (None, "percent", "flat")
VALID_STOCK_MODES = ("unit", "amount")


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
    *,
    product_group_id: int | None = None,
    stock_mode: str = "unit",
    stock_cost: int = 1,
    stock_label: str | None = None,
) -> int:
    row = await db.fetchone("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM products")
    position = row["p"] if row else 0
    return await db.execute(
        """
        INSERT INTO products
            (category_type_id, name, description, image_url, emoji, product_type, stock_type,
             stock_quantity, base_price, currency_label, position,
             product_group_id, stock_mode, stock_cost, stock_label)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            category_type_id, name, description, image_url, emoji, product_type, stock_type,
            stock_quantity, base_price, currency_label, position,
            product_group_id, stock_mode, max(1, int(stock_cost)), stock_label,
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
        "product_group_id", "stock_mode", "stock_cost", "stock_label",
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
    db: Database,
    category_type_id: int | None = None,
    visible_only: bool = False,
    *,
    product_group_id: int | None = None,
    ungrouped: bool = False,
    browsable_only: bool = False,
):
    """`product_group_id` = cuma produk di Product Type itu. `ungrouped` =
    cuma produk yang GAK masuk Product Type manapun. `browsable_only` =
    buang produk yang Product Type-nya lagi dinonaktifin (dipake tampilan
    customer, biar produk di Product Type nonaktif gak bocor ke daftar)."""
    query = "SELECT * FROM products WHERE 1 = 1"
    params: list = []
    if category_type_id is not None:
        query += " AND category_type_id = ?"
        params.append(category_type_id)
    if visible_only:
        query += " AND visible = 1"
    if product_group_id is not None:
        query += " AND product_group_id = ?"
        params.append(product_group_id)
    elif ungrouped:
        query += " AND product_group_id IS NULL"
    if browsable_only:
        query += (
            " AND (product_group_id IS NULL OR product_group_id IN "
            "(SELECT id FROM product_groups WHERE enabled = 1))"
        )
    query += " ORDER BY position ASC, id ASC"
    return await db.fetchall(query, tuple(params))


async def adjust_stock(db: Database, product_id: int, delta: int) -> None:
    """Increment/decrement manual stock. Never goes below zero."""
    await db.execute(
        "UPDATE products SET stock_quantity = MAX(0, stock_quantity + ?) WHERE id = ?",
        (delta, product_id),
    )


async def try_decrement_stock(db: Database, product_id: int, amount: int) -> bool:
    """Kurangin stok produk HANYA KALAU masih cukup (atomik, satu
    statement). Beda dari adjust_stock() yang gak pernah nolak -- ini yang
    dipake checkout biar dua pembeli barengan gak bisa dua-duanya lolos
    buat sisa stok yang sama."""
    changed = await db.execute_rowcount(
        "UPDATE products SET stock_quantity = stock_quantity - ? "
        "WHERE id = ? AND stock_type = 'manual' AND stock_quantity >= ?",
        (amount, product_id, amount),
    )
    return changed > 0


async def increment_stock(db: Database, product_id: int, amount: int) -> None:
    await db.execute(
        "UPDATE products SET stock_quantity = stock_quantity + ? WHERE id = ?", (amount, product_id)
    )


async def set_stock(db: Database, product_id: int, quantity: int) -> None:
    await db.execute(
        "UPDATE products SET stock_quantity = ? WHERE id = ?", (max(0, quantity), product_id)
    )


async def search_products(db: Database, term: str, limit: int = 25, visible_only: bool = False):
    query = "SELECT * FROM products WHERE name LIKE ?"
    params: list = [f"%{term}%"]
    if visible_only:
        query += " AND visible = 1"
    query += " ORDER BY name ASC LIMIT ?"
    params.append(limit)
    return await db.fetchall(query, tuple(params))
