"""
Query helpers for `product_fields` -- the admin-configurable dynamic input
fields (Username, User ID, Login Data, Email, Password, Server ID, Game ID,
Custom Text, ...) collected from the customer at checkout via Modals.

These live on the CATEGORY TYPE (not the product) so every product under a
type automatically shares the same checkout fields -- configure once per
type instead of once per product.

Optionally a PRODUCT TYPE (product_groups) can carry EXTRA fields of its
own (product_group_id set). They are ADDED on top of the category type's
fields, never replace them: a product under that Product Type is asked the
category type's fields first, then the Product Type's. list_fields() only
returns the category-type-level ones (product_group_id IS NULL) so every
existing caller keeps its behaviour; use list_group_fields() /
list_fields_for_product() for the Product Type level.
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
    product_group_id: int | None = None,
) -> int:
    """`product_group_id` diisi buat bikin field TAMBAHAN khusus Product
    Type (category_type_id tetep tipe induk Product Type itu)."""
    if product_group_id is None:
        row = await db.fetchone(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM product_fields "
            "WHERE category_type_id = ? AND product_group_id IS NULL",
            (category_type_id,),
        )
    else:
        row = await db.fetchone(
            "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM product_fields WHERE product_group_id = ?",
            (product_group_id,),
        )
    position = row["p"] if row else 0
    return await db.execute(
        """
        INSERT INTO product_fields
            (category_type_id, product_group_id, label, field_type, required, placeholder,
             min_length, max_length, validation, position)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            category_type_id, product_group_id, label, field_type, int(required), placeholder,
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
    """Field level Category Type SAJA (tanpa field tambahan Product Type)."""
    return await db.fetchall(
        "SELECT * FROM product_fields WHERE category_type_id = ? AND product_group_id IS NULL "
        "ORDER BY position ASC, id ASC",
        (category_type_id,),
    )


async def list_group_fields(db: Database, product_group_id: int):
    """Field tambahan khusus satu Product Type."""
    return await db.fetchall(
        "SELECT * FROM product_fields WHERE product_group_id = ? ORDER BY position ASC, id ASC",
        (product_group_id,),
    )


async def list_fields_for_product(
    db: Database, category_type_id: int, product_group_id: int | None
):
    """Semua field yang bakal ditanyain buat produk di Category Type +
    Product Type ini: field Category Type dulu, baru field tambahan
    Product Type-nya (kalau ada)."""
    fields = list(await list_fields(db, category_type_id))
    if product_group_id:
        fields.extend(await list_group_fields(db, product_group_id))
    return fields
