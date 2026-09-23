"""Query helpers for the `payment_methods` table."""

from __future__ import annotations

from bot.database.core import Database


async def create_payment_method(
    db: Database,
    name: str,
    instructions: str | None,
    timeout_minutes: int,
    image_url: str | None = None,
    emoji: str | None = None,
) -> int:
    row = await db.fetchone("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM payment_methods")
    position = row["p"] if row else 0
    return await db.execute(
        """
        INSERT INTO payment_methods (name, instructions, image_url, timeout_minutes, position, emoji)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, instructions, image_url, timeout_minutes, position, emoji),
    )


async def update_payment_method(db: Database, payment_id: int, **fields) -> None:
    allowed = {"name", "instructions", "image_url", "enabled", "timeout_minutes", "position", "emoji"}
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return
    params.append(payment_id)
    await db.execute(
        f"UPDATE payment_methods SET {', '.join(sets)} WHERE id = ?", tuple(params)
    )


async def delete_payment_method(db: Database, payment_id: int) -> None:
    await db.execute("DELETE FROM payment_methods WHERE id = ?", (payment_id,))


async def set_payment_enabled(db: Database, payment_id: int, enabled: bool) -> None:
    await db.execute(
        "UPDATE payment_methods SET enabled = ? WHERE id = ?", (int(enabled), payment_id)
    )


async def get_payment_method(db: Database, payment_id: int):
    return await db.fetchone("SELECT * FROM payment_methods WHERE id = ?", (payment_id,))


async def list_payment_methods(db: Database, enabled_only: bool = False):
    if enabled_only:
        return await db.fetchall(
            "SELECT * FROM payment_methods WHERE enabled = 1 ORDER BY position ASC, id ASC"
        )
    return await db.fetchall("SELECT * FROM payment_methods ORDER BY position ASC, id ASC")
