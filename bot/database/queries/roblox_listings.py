"""Queries buat listing item Roblox limited -- slideshow gambar + info
stock + tombol link. Lihat bot.cogs.roblox_listing & bot.ui.views.
RobloxListingView/RobloxSlideButton buat pemakaiannya."""

from __future__ import annotations

from bot.database.core import Database


# -- Listing --------------------------------------------------------------

async def create_listing(
    db: Database, guild_id: int, title: str, stock_info: str, link_label: str, link_url: str
) -> int:
    return await db.execute(
        """
        INSERT INTO roblox_listings (guild_id, title, stock_info, link_label, link_url)
        VALUES (?, ?, ?, ?, ?)
        """,
        (guild_id, title, stock_info, link_label, link_url),
    )


async def get_listing(db: Database, listing_id: int):
    return await db.fetchone("SELECT * FROM roblox_listings WHERE id = ?", (listing_id,))


async def list_listings(db: Database, guild_id: int):
    return await db.fetchall(
        "SELECT * FROM roblox_listings WHERE guild_id = ? ORDER BY created_at DESC", (guild_id,)
    )


async def update_listing(
    db: Database, listing_id: int, *,
    title: str | None = None, stock_info: str | None = None,
    link_label: str | None = None, link_url: str | None = None,
) -> None:
    """Cuma field yang diisi (bukan None) yang keupdate -- caller
    (bot.cogs.roblox_listing.edit) ngirim None buat parameter yang gak
    diubah staff."""
    fields = {"title": title, "stock_info": stock_info, "link_label": link_label, "link_url": link_url}
    fields = {k: v for k, v in fields.items() if v is not None}
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [listing_id]
    await db.execute(
        f"UPDATE roblox_listings SET {set_clause}, updated_at = datetime('now') WHERE id = ?", tuple(params)
    )


async def set_current_index(db: Database, listing_id: int, index: int) -> None:
    await db.execute("UPDATE roblox_listings SET current_index = ? WHERE id = ?", (index, listing_id))


async def set_message_ref(db: Database, listing_id: int, channel_id: int, message_id: int) -> None:
    await db.execute(
        "UPDATE roblox_listings SET channel_id = ?, message_id = ? WHERE id = ?",
        (channel_id, message_id, listing_id),
    )


async def delete_listing(db: Database, listing_id: int) -> None:
    # ON DELETE CASCADE di roblox_listing_images (lihat schema.sql) yang
    # nge-handle hapus gambar-gambarnya, gak perlu query terpisah di sini.
    await db.execute("DELETE FROM roblox_listings WHERE id = ?", (listing_id,))


# -- Gambar slideshow -------------------------------------------------------

async def add_image(db: Database, listing_id: int, image_url: str) -> None:
    row = await db.fetchone(
        "SELECT COALESCE(MAX(position), -1) AS max_pos FROM roblox_listing_images WHERE listing_id = ?",
        (listing_id,),
    )
    next_pos = (row["max_pos"] if row else -1) + 1
    await db.execute(
        "INSERT INTO roblox_listing_images (listing_id, image_url, position) VALUES (?, ?, ?)",
        (listing_id, image_url, next_pos),
    )


async def list_images(db: Database, listing_id: int):
    return await db.fetchall(
        "SELECT * FROM roblox_listing_images WHERE listing_id = ? ORDER BY position ASC", (listing_id,)
    )


async def remove_image_at(db: Database, listing_id: int, zero_based_index: int) -> bool:
    """Return True kalau gambarnya beneran ketemu & kehapus -- False
    kalau nomor yang staff kasih di luar jangkauan."""
    images = await list_images(db, listing_id)
    if zero_based_index < 0 or zero_based_index >= len(images):
        return False
    target = images[zero_based_index]
    await db.execute("DELETE FROM roblox_listing_images WHERE id = ?", (target["id"],))
    return True
