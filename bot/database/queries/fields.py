"""
Query helpers for `product_fields` -- the admin-configurable dynamic input
fields (Username, User ID, Login Data, Email, Password, Server ID, Game ID,
Custom Text, ...) collected from the customer at checkout via Modals.

These live on the CATEGORY TYPE (not the product) so every product under a
type automatically shares the same checkout fields -- configure once per
type instead of once per product.
"""

from __future__ import annotations

from bot.database.core import Database

FIELD_TYPES = (
    "username", "userid", "login", "email", "password",
    "serverid", "gameid", "custom",
)
VALIDATIONS = ("none", "numeric", "alpha", "alphanumeric", "email")

# Discord Modals support a maximum of 5 text input components, so when a
# category type has more than 5 fields configured we chain modals in batches.
MODAL_BATCH_SIZE = 5


async def create_field(
    db: Database,
    category_type_id: int,
    label: str,
    field_type: str,
    required: bool,
    placeholder: str | None,
    min_length: int,
    max_length: int,
    validation: str,
) -> int:
    row = await db.fetchone(
        "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM product_fields WHERE category_type_id = ?",
        (category_type_id,),
    )
    position = row["p"] if row else 0
    return await db.execute(
        """
        INSERT INTO product_fields
            (category_type_id, label, field_type, required, placeholder, min_length,
             max_length, validation, position)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            category_type_id, label, field_type, int(required), placeholder,
            min_length, max_length, validation, position,
        ),
    )


async def update_field(db: Database, field_id: int, **fields) -> None:
    allowed = {
        "label", "field_type", "required", "placeholder", "min_length",
        "max_length", "validation", "position",
    }
    sets, params = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        params.append(value)
    if not sets:
        return
    params.append(field_id)
    await db.execute(
        f"UPDATE product_fields SET {', '.join(sets)} WHERE id = ?", tuple(params)
    )


async def delete_field(db: Database, field_id: int) -> None:
    await db.execute("DELETE FROM product_fields WHERE id = ?", (field_id,))


async def get_field(db: Database, field_id: int):
    return await db.fetchone("SELECT * FROM product_fields WHERE id = ?", (field_id,))


async def list_fields(db: Database, category_type_id: int):
    return await db.fetchall(
        "SELECT * FROM product_fields WHERE category_type_id = ? ORDER BY position ASC, id ASC",
        (category_type_id,),
    )
