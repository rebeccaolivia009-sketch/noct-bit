"""
Query helpers for `product_groups` -- "Product Type" yang OPSIONAL, level
di antara Category Type dan Product (Category -> Category Type -> Product
Type -> Product).

Product Type juga bisa megang pool stok bersama (shared_stock = 1), jadi
semua produk di bawahnya narik dari satu stok yang sama -- misal stok
Robux 10.000 yang kepotong tiap ada yang beli paket 100/500/1000 Robux.
Aturan resolusi stok-nya ada di bot.utils.stock.
"""

from __future__ import annotations

from bot.database.core import Database

STOCK_MODES = ("unit", "amount")


async def create_product_group(
    db: Database,
    category_type_id: int,
    name: str,
    description: str | None = None,
    emoji: str | None = None,
    *,
    shared_stock: bool = False,
    stock_mode: str = "unit",
    stock_quantity: int = 0,
    stock_label: str | None = None,
) -> int:
    row = await db.fetchone(
        "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM product_groups WHERE category_type_id = ?",
        (category_type_id,),
    )
    position = row["p"] if row else 0
    return await db.execute(
        """
        INSERT INTO product_groups
            (category_type_id, name, description, emoji, position,
             shared_stock, stock_mode, stock_quantity, stock_label)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            category_type_id, name, description, emoji, position,
            int(shared_stock), stock_mode, stock_quantity, stock_label,
        ),
    )


async def update_product_group(db: Database, product_group_id: int, **fields) -> None:
    """Partial update. Nilai None TETEP ditulis (jadi NULL) -- caller yang
    milih field mana aja yang dikirim, jadi bisa ngosongin emoji/label."""
    allowed = {
        "name", "description", "emoji", "position", "enabled",
        "shared_stock", "stock_mode", "stock_quantity", "stock_label",
    }
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return
    params.append(product_group_id)
    await db.execute(f"UPDATE product_groups SET {', '.join(sets)} WHERE id = ?", tuple(params))


async def delete_product_group(db: Database, product_group_id: int) -> None:
    """Produk di bawahnya GAK ikut kehapus -- product_group_id mereka
    jadi NULL (ON DELETE SET NULL), cuma kelepas dari Product Type ini."""
    await db.execute("DELETE FROM product_groups WHERE id = ?", (product_group_id,))


async def set_product_group_enabled(db: Database, product_group_id: int, enabled: bool) -> None:
    await db.execute(
        "UPDATE product_groups SET enabled = ? WHERE id = ?", (int(enabled), product_group_id)
    )


async def set_product_group_position(db: Database, product_group_id: int, position: int) -> None:
    await db.execute(
        "UPDATE product_groups SET position = ? WHERE id = ?", (position, product_group_id)
    )


async def get_product_group(db: Database, product_group_id: int):
    return await db.fetchone("SELECT * FROM product_groups WHERE id = ?", (product_group_id,))


async def list_product_groups(
    db: Database, category_type_id: int | None = None, enabled_only: bool = False
):
    query = "SELECT * FROM product_groups WHERE 1 = 1"
    params: list = []
    if category_type_id is not None:
        query += " AND category_type_id = ?"
        params.append(category_type_id)
    if enabled_only:
        query += " AND enabled = 1"
    query += " ORDER BY position ASC, id ASC"
    return await db.fetchall(query, tuple(params))


async def search_product_groups(
    db: Database, term: str, category_type_id: int | None = None, limit: int = 25
):
    query = "SELECT * FROM product_groups WHERE name LIKE ?"
    params: list = [f"%{term}%"]
    if category_type_id is not None:
        query += " AND category_type_id = ?"
        params.append(category_type_id)
    query += " ORDER BY name ASC LIMIT ?"
    params.append(limit)
    return await db.fetchall(query, tuple(params))


# -- Pool stok bersama --------------------------------------------------------

async def try_decrement_stock(db: Database, product_group_id: int, amount: int) -> bool:
    """Kurangin pool HANYA KALAU masih cukup -- satu statement atomik, jadi
    dua pembeli barengan gak bisa dua-duanya lolos buat sisa stok yang sama."""
    changed = await db.execute_rowcount(
        "UPDATE product_groups SET stock_quantity = stock_quantity - ? "
        "WHERE id = ? AND shared_stock = 1 AND stock_quantity >= ?",
        (amount, product_group_id, amount),
    )
    return changed > 0


async def increment_stock(db: Database, product_group_id: int, amount: int) -> None:
    await db.execute(
        "UPDATE product_groups SET stock_quantity = stock_quantity + ? WHERE id = ?",
        (amount, product_group_id),
    )


async def set_stock(db: Database, product_group_id: int, quantity: int) -> None:
    await db.execute(
        "UPDATE product_groups SET stock_quantity = ? WHERE id = ?", (max(0, quantity), product_group_id)
    )
