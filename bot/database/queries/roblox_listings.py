"""Queries buat katalog item Roblox limited -- SATU panel, geser
Sebelumnya/Selanjutnya buat pindah ANTAR ITEM (beda item, beda gambar,
beda stock/harga, beda link). Lihat bot.cogs.roblox_listing & bot.ui.
views.RobloxCatalogView/RobloxSlideButton buat pemakaiannya."""

from __future__ import annotations

from bot.database.core import Database


# -- Katalog (panel) --------------------------------------------------------

async def create_catalog(db: Database, guild_id: int, panel_title: str) -> int:
    return await db.execute(
        "INSERT INTO roblox_catalogs (guild_id, panel_title) VALUES (?, ?)",
        (guild_id, panel_title),
    )


async def get_catalog(db: Database, catalog_id: int):
    return await db.fetchone("SELECT * FROM roblox_catalogs WHERE id = ?", (catalog_id,))


async def list_catalogs(db: Database, guild_id: int):
    return await db.fetchall(
        "SELECT * FROM roblox_catalogs WHERE guild_id = ? ORDER BY created_at DESC", (guild_id,)
    )


async def set_current_index(db: Database, catalog_id: int, index: int) -> None:
    await db.execute("UPDATE roblox_catalogs SET current_index = ? WHERE id = ?", (index, catalog_id))


async def set_message_ref(db: Database, catalog_id: int, channel_id: int, message_id: int) -> None:
    await db.execute(
        "UPDATE roblox_catalogs SET channel_id = ?, message_id = ? WHERE id = ?",
        (channel_id, message_id, catalog_id),
    )


async def delete_catalog(db: Database, catalog_id: int) -> None:
    # ON DELETE CASCADE di roblox_catalog_items (lihat schema.sql) yang
    # nge-handle hapus item-itemnya, gak perlu query terpisah di sini.
    await db.execute("DELETE FROM roblox_catalogs WHERE id = ?", (catalog_id,))


# -- Item (tiap slide) --------------------------------------------------------

async def add_item(
    db: Database, catalog_id: int, item_title: str, image_url: str,
    stock_info: str, link_label: str, link_url: str,
) -> int:
    row = await db.fetchone(
        "SELECT COALESCE(MAX(position), -1) AS max_pos FROM roblox_catalog_items WHERE catalog_id = ?",
        (catalog_id,),
    )
    next_pos = (row["max_pos"] if row else -1) + 1
    return await db.execute(
        """
        INSERT INTO roblox_catalog_items
            (catalog_id, item_title, image_url, stock_info, link_label, link_url, position)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (catalog_id, item_title, image_url, stock_info, link_label, link_url, next_pos),
    )


async def list_items(db: Database, catalog_id: int):
    return await db.fetchall(
        "SELECT * FROM roblox_catalog_items WHERE catalog_id = ? ORDER BY position ASC", (catalog_id,)
    )


async def get_item_at(db: Database, catalog_id: int, zero_based_index: int):
    items = await list_items(db, catalog_id)
    if not items:
        return None
    return items[zero_based_index % len(items)]


async def remove_item_at(db: Database, catalog_id: int, zero_based_index: int) -> bool:
    """Return True kalau item-nya beneran ketemu & kehapus -- False kalau
    nomor yang staff kasih di luar jangkauan."""
    items = await list_items(db, catalog_id)
    if zero_based_index < 0 or zero_based_index >= len(items):
        return False
    target = items[zero_based_index]
    await db.execute("DELETE FROM roblox_catalog_items WHERE id = ?", (target["id"],))
    return True


async def update_item_at(
    db: Database, catalog_id: int, zero_based_index: int, *,
    item_title: str | None = None, stock_info: str | None = None,
    link_label: str | None = None, link_url: str | None = None,
) -> bool:
    """Cuma field yang diisi (bukan None) yang keupdate. Return True kalau
    nomor item-nya valid, False kalau enggak."""
    items = await list_items(db, catalog_id)
    if zero_based_index < 0 or zero_based_index >= len(items):
        return False
    target_id = items[zero_based_index]["id"]

    fields = {"item_title": item_title, "stock_info": stock_info, "link_label": link_label, "link_url": link_url}
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        return True
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [target_id]
    await db.execute(f"UPDATE roblox_catalog_items SET {set_clause} WHERE id = ?", tuple(params))
    return True
