"""Queries buat catatan kartu MANUAL (/manual payment|testi|rating) -- order
yang diproses lewat ticket (produk di luar panel shop), bukan lewat alur
checkout bot. Dicatat buat nomor urut (#M1, #M2, ... per jenis) dan riwayat
audit siapa staff yang bikin.

Tabel dibuat sendiri (CREATE TABLE IF NOT EXISTS) -- gak perlu nyentuh
schema.sql/core.py, dan otomatis dibikin lagi kalau database di-import lewat
/backup import."""

from __future__ import annotations

from bot.database.core import Database

KINDS = ("payment", "testi", "rating")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS manual_entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    kind            TEXT NOT NULL,
    number          INTEGER NOT NULL,
    staff_id        INTEGER NOT NULL,
    customer_id     INTEGER,
    customer_label  TEXT,
    product         TEXT NOT NULL,
    amount          REAL,
    currency        TEXT,
    rating          INTEGER,
    note            TEXT,
    channel_id      INTEGER,
    message_id      INTEGER,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

_ensured_conn = None


async def ensure_tables(db: Database) -> None:
    global _ensured_conn
    if db.conn is not None and _ensured_conn is db.conn:
        return
    await db.execute(_SCHEMA)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_manual_entries_kind ON manual_entries(kind, number)")
    _ensured_conn = db.conn


async def create_entry(
    db: Database, kind: str, staff_id: int, product: str, *, customer_id: int | None = None,
    customer_label: str | None = None, amount: float | None = None, currency: str | None = None,
    rating: int | None = None, note: str | None = None,
):
    """Bikin catatan baru dan balikin row-nya (termasuk `number` urut per
    jenis). Nomornya dihitung DI DALAM satu statement INSERT biar dua command
    yang jalan bersamaan gak pernah dapet nomor yang sama."""
    await ensure_tables(db)
    entry_id = await db.execute(
        """
        INSERT INTO manual_entries
            (kind, number, staff_id, customer_id, customer_label, product, amount, currency, rating, note)
        VALUES (?, (SELECT COALESCE(MAX(number), 0) + 1 FROM manual_entries WHERE kind = ?),
                ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (kind, kind, staff_id, customer_id, customer_label, product, amount, currency, rating, note),
    )
    return await db.fetchone("SELECT * FROM manual_entries WHERE id = ?", (entry_id,))


async def set_message(db: Database, entry_id: int, channel_id: int, message_id: int) -> None:
    await db.execute(
        "UPDATE manual_entries SET channel_id = ?, message_id = ? WHERE id = ?", (channel_id, message_id, entry_id)
    )


async def delete_entry(db: Database, entry_id: int) -> None:
    """Dipake kalau pengiriman kartunya gagal -- jangan ninggalin catatan
    (dan nomor urut yang kebuang) buat kartu yang gak pernah terkirim."""
    await db.execute("DELETE FROM manual_entries WHERE id = ?", (entry_id,))


async def list_recent(db: Database, kind: str | None, limit: int = 10):
    await ensure_tables(db)
    return await db.fetchall(
        "SELECT * FROM manual_entries WHERE (? IS NULL OR kind = ?) ORDER BY id DESC LIMIT ?",
        (kind, kind, limit),
    )


async def list_active_payment_methods(db: Database):
    """Metode pembayaran aktif (tabel payment_methods yang diatur lewat
    command payment method yang udah ada) -- query langsung biar gak
    bergantung ke modul queries lain."""
    return await db.fetchall("SELECT * FROM payment_methods WHERE enabled = 1 ORDER BY position ASC, id ASC")


async def get_active_payment_method(db: Database, method_id: int):
    return await db.fetchone("SELECT * FROM payment_methods WHERE id = ? AND enabled = 1", (method_id,))
