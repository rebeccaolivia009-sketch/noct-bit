"""Query helpers for `custom_order_config` -- pengaturan "Produk Lainnya"."""

from __future__ import annotations

from bot.database.core import Database

_COLUMNS = (
    "enabled", "product_id", "label", "description", "emoji", "price_mode",
    "fixed_price", "min_amount", "max_amount", "price_label", "currency_label",
)


async def get_config(db: Database):
    return await db.fetchone("SELECT * FROM custom_order_config WHERE id = 1")


async def save_config(db: Database, values: dict) -> None:
    """Simpen kolom yang dikirim (whitelist) -- baris dibikin kalau belum ada."""
    await db.execute("INSERT OR IGNORE INTO custom_order_config (id) VALUES (1)")
    cleaned = {k: v for k, v in values.items() if k in _COLUMNS}
    if not cleaned:
        return
    sets = ", ".join(f"{k} = ?" for k in cleaned)
    await db.execute(
        f"UPDATE custom_order_config SET {sets}, updated_at = datetime('now') WHERE id = 1",
        tuple(cleaned.values()),
    )
