"""Query helpers for `custom_quotes` -- permintaan "Produk Lainnya" yang
nunggu harga dari staff (lihat bot.utils.custom_order dan bot.ui.quotes)."""

from __future__ import annotations

import json

from bot.database.core import Database

OPEN_STATUSES = ("pending", "priced")


def fields_of(row) -> list[dict]:
    """Jawaban form dari satu baris (JSON -> list dict label/field_type/value)."""
    try:
        value = json.loads(row["fields_json"])
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


async def create(
    db: Database, user_id: int, product_id: int, fields: list[dict], currency_label: str | None
) -> int:
    clean = [
        {"label": f["label"], "field_type": f["field_type"], "value": f["value"]} for f in fields
    ]
    return await db.execute(
        "INSERT INTO custom_quotes (user_id, product_id, fields_json, currency_label) VALUES (?, ?, ?, ?)",
        (user_id, product_id, json.dumps(clean, ensure_ascii=False), currency_label),
    )


async def get(db: Database, quote_id: int):
    return await db.fetchone("SELECT * FROM custom_quotes WHERE id = ?", (quote_id,))


async def set_staff_message(db: Database, quote_id: int, channel_id: int, message_id: int) -> None:
    await db.execute(
        "UPDATE custom_quotes SET staff_channel_id = ?, staff_message_id = ? WHERE id = ?",
        (channel_id, message_id, quote_id),
    )


async def set_dm_message(db: Database, quote_id: int, channel_id: int | None, message_id: int | None) -> None:
    await db.execute(
        "UPDATE custom_quotes SET dm_channel_id = ?, dm_message_id = ? WHERE id = ?",
        (channel_id, message_id, quote_id),
    )


async def set_price(db: Database, quote_id: int, price: float, note: str | None) -> bool:
    """Kasih/ubah harga. Cuma boleh selama permintaan masih terbuka
    (pending/priced) -- yang udah jadi order / ditolak / dibatalin gak bisa."""
    changed = await db.execute_rowcount(
        "UPDATE custom_quotes SET price = ?, note = ?, status = 'priced', updated_at = datetime('now') "
        "WHERE id = ? AND status IN ('pending', 'priced')",
        (price, note, quote_id),
    )
    return changed > 0


async def transition(db: Database, quote_id: int, new_status: str, allowed_from: tuple[str, ...]) -> bool:
    """Ubah status HANYA KALAU statusnya sekarang ada di `allowed_from` (atomik)."""
    placeholders = ",".join("?" for _ in allowed_from)
    changed = await db.execute_rowcount(
        f"UPDATE custom_quotes SET status = ?, updated_at = datetime('now') "
        f"WHERE id = ? AND status IN ({placeholders})",
        (new_status, quote_id, *allowed_from),
    )
    return changed > 0


async def claim(db: Database, quote_id: int) -> bool:
    """Kunci penawaran buat SATU pembayaran. Dua klik "Lanjut Bayar" barengan
    gak bisa dua-duanya jadi order -- cuma satu yang menang."""
    return await transition(db, quote_id, "ordering", ("priced",))


async def release(db: Database, quote_id: int) -> bool:
    """Lepas kunci kalau pembayaran gagal/batal. No-op kalau udah 'ordered'."""
    return await transition(db, quote_id, "priced", ("ordering",))


async def mark_ordered(db: Database, quote_id: int, order_id: int) -> bool:
    changed = await db.execute_rowcount(
        "UPDATE custom_quotes SET status = 'ordered', order_id = ?, updated_at = datetime('now') "
        "WHERE id = ? AND status = 'ordering'",
        (order_id, quote_id),
    )
    return changed > 0


async def count_open(db: Database) -> tuple[int, int]:
    """(nunggu harga dari staff, nunggu keputusan customer)."""
    row = await db.fetchone(
        "SELECT SUM(status = 'pending') AS p, SUM(status = 'priced') AS q FROM custom_quotes"
    )
    return (int(row["p"] or 0), int(row["q"] or 0)) if row else (0, 0)
