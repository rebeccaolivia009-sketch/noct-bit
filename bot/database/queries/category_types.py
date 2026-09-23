"""
Query helpers for `category_types` -- the level between Category and
Product (Category -> Category Type -> Product). Replaces the old
per-product "variant" concept: rather than one product carrying several
priced sub-options, products are grouped under a type and each product is
its own fully independent, fully priced item.
"""

from __future__ import annotations

from bot.database.core import Database


async def create_category_type(
    db: Database, category_id: int, name: str, description: str | None, emoji: str | None = None
) -> int:
    row = await db.fetchone(
        "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM category_types WHERE category_id = ?",
        (category_id,),
    )
    position = row["p"] if row else 0
    return await db.execute(
        "INSERT INTO category_types (category_id, name, description, emoji, position) VALUES (?, ?, ?, ?, ?)",
        (category_id, name, description, emoji, position),
    )


async def update_category_type(
    db: Database,
    category_type_id: int,
    name: str | None = None,
    description: str | None = None,
    emoji: str | None = None,
    category_id: int | None = None,
) -> None:
    fields, params = [], []
    if name is not None:
        fields.append("name = ?")
        params.append(name)
    if description is not None:
        fields.append("description = ?")
        params.append(description)
    if emoji is not None:
        fields.append("emoji = ?")
        params.append(None if emoji == "none" else emoji)
    if category_id is not None:
        fields.append("category_id = ?")
        params.append(category_id)
    if not fields:
        return
    params.append(category_type_id)
    await db.execute(f"UPDATE category_types SET {', '.join(fields)} WHERE id = ?", tuple(params))


async def delete_category_type(db: Database, category_type_id: int) -> None:
    await db.execute("DELETE FROM category_types WHERE id = ?", (category_type_id,))


async def set_category_type_enabled(db: Database, category_type_id: int, enabled: bool) -> None:
    await db.execute(
        "UPDATE category_types SET enabled = ? WHERE id = ?", (int(enabled), category_type_id)
    )


async def set_category_type_position(db: Database, category_type_id: int, position: int) -> None:
    await db.execute(
        "UPDATE category_types SET position = ? WHERE id = ?", (position, category_type_id)
    )


async def get_category_type(db: Database, category_type_id: int):
    return await db.fetchone("SELECT * FROM category_types WHERE id = ?", (category_type_id,))


async def list_category_types(
    db: Database, category_id: int | None = None, enabled_only: bool = False
):
    query = "SELECT * FROM category_types WHERE 1 = 1"
    params: list = []
    if category_id is not None:
        query += " AND category_id = ?"
        params.append(category_id)
    if enabled_only:
        query += " AND enabled = 1"
    query += " ORDER BY position ASC, id ASC"
    return await db.fetchall(query, tuple(params))


async def search_category_types(db: Database, term: str, limit: int = 25):
    return await db.fetchall(
        "SELECT * FROM category_types WHERE name LIKE ? ORDER BY name ASC LIMIT ?",
        (f"%{term}%", limit),
    )
