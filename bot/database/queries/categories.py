"""Query helpers for the `categories` table."""

from __future__ import annotations

from bot.database.core import Database


async def create_category(
    db: Database, name: str, description: str | None, emoji: str | None = None
) -> int:
    row = await db.fetchone("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM categories")
    position = row["p"] if row else 0
    return await db.execute(
        "INSERT INTO categories (name, description, emoji, position) VALUES (?, ?, ?, ?)",
        (name, description, emoji, position),
    )


async def update_category(
    db: Database,
    category_id: int,
    name: str | None = None,
    description: str | None = None,
    emoji: str | None = None,
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
    if not fields:
        return
    params.append(category_id)
    await db.execute(f"UPDATE categories SET {', '.join(fields)} WHERE id = ?", tuple(params))


async def delete_category(db: Database, category_id: int) -> None:
    await db.execute("DELETE FROM categories WHERE id = ?", (category_id,))


async def set_category_enabled(db: Database, category_id: int, enabled: bool) -> None:
    await db.execute(
        "UPDATE categories SET enabled = ? WHERE id = ?", (int(enabled), category_id)
    )


async def set_category_position(db: Database, category_id: int, position: int) -> None:
    await db.execute(
        "UPDATE categories SET position = ? WHERE id = ?", (position, category_id)
    )


async def get_category(db: Database, category_id: int):
    return await db.fetchone("SELECT * FROM categories WHERE id = ?", (category_id,))


async def list_categories(db: Database, enabled_only: bool = False):
    if enabled_only:
        return await db.fetchall(
            "SELECT * FROM categories WHERE enabled = 1 ORDER BY position ASC, id ASC"
        )
    return await db.fetchall("SELECT * FROM categories ORDER BY position ASC, id ASC")
