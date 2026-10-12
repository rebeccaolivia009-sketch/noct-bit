"""Queries buat SLA staff, statistik staff, dan pengingat bayar.

Tabel-tabelnya dibuat sendiri (CREATE TABLE IF NOT EXISTS) pas pertama kali
dipake -- gak perlu nyentuh schema.sql/core.py, dan otomatis dibikin ulang
kalau database di-import lewat /backup import (koneksi baru = dicek lagi).

  order_events      -- jejak aksi staff per order (bukti bayar masuk, lunas,
                       selesai, batal, refund) + siapa pelakunya. Dicatat dari
                       bot.utils.order_actions lewat bot.utils.order_events.
                       Data mulai terkumpul SEJAK fitur ini dipasang.
  ticket_responses  -- balasan staff PERTAMA di tiap ticket (buat waktu respon).
  sla_reminders     -- udah berapa kali order/ticket tertentu diingatkan.
  pay_reminders     -- order yang udah diingatkan soal batas bayar.

Semua timestamp UTC format SQLite 'YYYY-MM-DD HH:MM:SS' (sama kayak kolom
created_at di tabel lain), jadi bisa dibandingin sebagai teks."""

from __future__ import annotations

from bot.database.core import Database

_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS order_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id    INTEGER NOT NULL,
        kind        TEXT NOT NULL,
        actor_id    INTEGER,
        created_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_order_events_order ON order_events(order_id, kind)",
    "CREATE INDEX IF NOT EXISTS idx_order_events_actor ON order_events(actor_id, kind, created_at)",
    """CREATE TABLE IF NOT EXISTS ticket_responses (
        ticket_id     INTEGER PRIMARY KEY,
        staff_id      INTEGER NOT NULL,
        responded_at  TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    """CREATE TABLE IF NOT EXISTS sla_reminders (
        kind          TEXT NOT NULL,
        ref_id        INTEGER NOT NULL,
        count         INTEGER NOT NULL DEFAULT 0,
        last_sent_at  TEXT NOT NULL,
        PRIMARY KEY (kind, ref_id)
    )""",
    """CREATE TABLE IF NOT EXISTS pay_reminders (
        order_id  INTEGER PRIMARY KEY,
        sent_at   TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
)

_ensured_conn = None


async def ensure_tables(db: Database) -> None:
    """Idempoten & murah: cuma bener-bener jalan sekali per koneksi database
    (koneksi baru abis /backup import = dicek lagi)."""
    global _ensured_conn
    if db.conn is not None and _ensured_conn is db.conn:
        return
    for statement in _STATEMENTS:
        await db.execute(statement)
    _ensured_conn = db.conn


# ============================================================================
# Event order
# ============================================================================

async def record_order_event(db: Database, order_id: int, kind: str, actor_id: int | None) -> None:
    await ensure_tables(db)
    await db.execute(
        "INSERT INTO order_events (order_id, kind, actor_id) VALUES (?, ?, ?)", (order_id, kind, actor_id)
    )


async def list_proofs_waiting_verification(db: Database):
    """Order yang bukti bayarnya udah masuk tapi belum diverifikasi staff
    (status & payment masih pending). `since` = kapan bukti PERTAMA masuk."""
    await ensure_tables(db)
    return await db.fetchall(
        """
        SELECT o.id, o.user_id, o.total_price, o.currency_label, e.created_at AS since
        FROM orders o
        JOIN (SELECT order_id, MIN(created_at) AS created_at FROM order_events
              WHERE kind = 'proof' GROUP BY order_id) e ON e.order_id = o.id
        WHERE o.status = 'pending' AND o.payment_status = 'pending'
        ORDER BY e.created_at ASC
        """
    )


async def list_paid_not_completed(db: Database):
    """Order yang udah lunas tapi belum selesai/dibatalin/di-refund. `since`
    = kapan ditandain lunas (dari event; fallback ke updated_at buat order
    yang lunas sebelum fitur ini dipasang)."""
    await ensure_tables(db)
    return await db.fetchall(
        """
        SELECT o.id, o.user_id, o.total_price, o.currency_label,
               COALESCE((SELECT MAX(created_at) FROM order_events
                         WHERE order_id = o.id AND kind = 'paid'), o.updated_at) AS since
        FROM orders o
        WHERE o.payment_status = 'paid' AND o.status IN ('pending', 'processing')
        ORDER BY since ASC
        """
    )


async def list_tickets_without_response(db: Database):
    """Ticket yang masih open, belum dibalas staff, dan belum di-claim."""
    await ensure_tables(db)
    return await db.fetchall(
        """
        SELECT t.id, t.channel_id, t.user_id, t.kind, t.created_at AS since
        FROM tickets t
        LEFT JOIN ticket_responses r ON r.ticket_id = t.id
        WHERE t.status = 'open' AND r.ticket_id IS NULL AND t.claimed_by IS NULL
        ORDER BY t.created_at ASC
        """
    )


# ============================================================================
# Respon ticket
# ============================================================================

async def get_open_ticket_by_channel(db: Database, channel_id: int):
    await ensure_tables(db)
    return await db.fetchone(
        "SELECT id, user_id FROM tickets WHERE channel_id = ? AND status = 'open'", (channel_id,)
    )


async def record_first_response(db: Database, ticket_id: int, staff_id: int) -> bool:
    """True kalau INI balasan staff pertama di ticket itu (baru kecatat)."""
    await ensure_tables(db)
    existing = await db.fetchone("SELECT 1 FROM ticket_responses WHERE ticket_id = ?", (ticket_id,))
    if existing:
        return False
    await db.execute(
        "INSERT OR IGNORE INTO ticket_responses (ticket_id, staff_id) VALUES (?, ?)", (ticket_id, staff_id)
    )
    return True


# ============================================================================
# Pengingat SLA
# ============================================================================

async def get_sla_state(db: Database, kind: str, ref_id: int):
    await ensure_tables(db)
    return await db.fetchone("SELECT count, last_sent_at FROM sla_reminders WHERE kind = ? AND ref_id = ?", (kind, ref_id))


async def bump_sla_state(db: Database, kind: str, ref_id: int) -> None:
    await ensure_tables(db)
    await db.execute(
        """
        INSERT INTO sla_reminders (kind, ref_id, count, last_sent_at) VALUES (?, ?, 1, datetime('now'))
        ON CONFLICT(kind, ref_id) DO UPDATE SET count = count + 1, last_sent_at = datetime('now')
        """,
        (kind, ref_id),
    )


# ============================================================================
# Pengingat batas bayar
# ============================================================================

async def list_orders_near_payment_deadline(db: Database, within_minutes: int):
    """Order yang BELUM bayar (belum ada bukti), batas bayarnya dalam
    `within_minutes` menit ke depan, dan belum pernah diingatkan.
    datetime() SQLite dipake buat menormalkan payment_deadline, jadi format
    'YYYY-MM-DD HH:MM:SS' maupun ISO 8601 ('...T...') sama-sama kebaca (waktu
    dianggap UTC, sama kayak datetime('now'))."""
    await ensure_tables(db)
    return await db.fetchall(
        """
        SELECT o.id, o.user_id, o.total_price, o.currency_label, o.payment_deadline
        FROM orders o
        WHERE o.status = 'pending' AND o.payment_status = 'pending'
          AND o.payment_deadline IS NOT NULL
          AND (o.payment_proof_url IS NULL OR o.payment_proof_url = '')
          AND datetime(o.payment_deadline) > datetime('now')
          AND datetime(o.payment_deadline) <= datetime('now', ?)
          AND datetime(o.created_at) <= datetime('now', '-2 minutes')
          AND NOT EXISTS (SELECT 1 FROM pay_reminders p WHERE p.order_id = o.id)
        ORDER BY datetime(o.payment_deadline) ASC
        """,
        (f"+{int(within_minutes)} minutes",),
    )


async def mark_pay_reminded(db: Database, order_id: int) -> None:
    await ensure_tables(db)
    await db.execute("INSERT OR IGNORE INTO pay_reminders (order_id) VALUES (?)", (order_id,))


# ============================================================================
# Statistik staff
# ============================================================================

async def order_event_counts(db: Database, since: str | None):
    """[(actor_id, kind, jumlah)] -- cuma aksi yang pelakunya staff."""
    await ensure_tables(db)
    return await db.fetchall(
        """
        SELECT actor_id, kind, COUNT(*) AS n FROM order_events
        WHERE actor_id IS NOT NULL AND (? IS NULL OR created_at >= ?)
        GROUP BY actor_id, kind
        """,
        (since, since),
    )


async def handling_seconds(db: Database, since: str | None):
    """[(actor_id, detik)] dari lunas -> selesai per order yang diselesaiin staff."""
    await ensure_tables(db)
    return await db.fetchall(
        """
        SELECT c.actor_id AS actor_id,
               (julianday(c.created_at) - julianday(
                    (SELECT MAX(p.created_at) FROM order_events p
                     WHERE p.order_id = c.order_id AND p.kind = 'paid' AND p.created_at <= c.created_at)
               )) * 86400.0 AS secs
        FROM order_events c
        WHERE c.kind = 'completed' AND c.actor_id IS NOT NULL AND (? IS NULL OR c.created_at >= ?)
        """,
        (since, since),
    )


async def response_seconds(db: Database, since: str | None):
    """[(staff_id, detik)] dari ticket dibuka -> balasan staff pertama."""
    await ensure_tables(db)
    return await db.fetchall(
        """
        SELECT r.staff_id AS staff_id,
               (julianday(r.responded_at) - julianday(t.created_at)) * 86400.0 AS secs
        FROM ticket_responses r JOIN tickets t ON t.id = r.ticket_id
        WHERE (? IS NULL OR r.responded_at >= ?)
        """,
        (since, since),
    )


async def shipment_counts(db: Database, since: str | None):
    """[(staff_user_id, jumlah)] bukti pengiriman -- data HISTORIS, udah ada
    sebelum fitur statistik ini dipasang."""
    return await db.fetchall(
        """
        SELECT staff_user_id, COUNT(*) AS n FROM shipment_proofs
        WHERE (? IS NULL OR created_at >= ?) GROUP BY staff_user_id
        """,
        (since, since),
    )


async def claim_counts(db: Database, since: str | None):
    """[(claimed_by, jumlah)] ticket yang di-claim -- data HISTORIS."""
    return await db.fetchall(
        """
        SELECT claimed_by, COUNT(*) AS n FROM tickets
        WHERE claimed_by IS NOT NULL AND (? IS NULL OR created_at >= ?) GROUP BY claimed_by
        """,
        (since, since),
    )
