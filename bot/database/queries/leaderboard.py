"""Queries buat leaderboard Top Spenders -- agregat dari `orders` (siapa
paling banyak belanja) + badge custom leaderboard (top 1-3 doang, lihat
bot.cogs.badge) + ID pesan leaderboard yang lagi aktif (dipake caller buat
edit-in-place tiap refresh, bukan kirim pesan baru tiap kali)."""

from __future__ import annotations

from bot.database.core import Database
from bot.database.queries import settings as settings_q


# ============================================================================
# TOP SPENDERS -- agregat dari orders
# ============================================================================

async def get_top_spenders(
    db: Database, limit: int = 10, excluded_user_ids: list[int] | None = None
):
    """Ranking user berdasarkan total belanja -- CUMA ngitung order yang
    status='completed' DAN payment_status='paid' (order pending/cancelled/
    refunded gak pernah kehitung di sini dari awal). `excluded_user_ids`
    (dari /settings leaderboard_exclude) di-filter di level SQL biar akun
    staff/tester yang dikecualiin gak numpang keitung sama sekali, bukan
    cuma disembunyiin belakangan.

    `currency_label` diambil dari order TERBARU user itu (bukan MAX/MIN
    alfabetis) -- asumsinya satu toko satu mata uang tetep, tapi ini jaga-
    jaga kalau currency_label pernah ganti di histori order lama."""
    excluded_user_ids = excluded_user_ids or []
    params: list = []

    exclude_clause = ""
    if excluded_user_ids:
        placeholders = ",".join("?" for _ in excluded_user_ids)
        exclude_clause = f"AND user_id NOT IN ({placeholders})"
        params.extend(excluded_user_ids)

    query = f"""
        SELECT
            user_id,
            SUM(total_price) AS total_spent,
            COUNT(*) AS total_orders,
            (
                SELECT o2.currency_label FROM orders o2
                WHERE o2.user_id = o.user_id
                  AND o2.status = 'completed' AND o2.payment_status = 'paid'
                ORDER BY o2.created_at DESC
                LIMIT 1
            ) AS currency_label
        FROM orders o
        WHERE status = 'completed' AND payment_status = 'paid'
        {exclude_clause}
        GROUP BY user_id
        ORDER BY total_spent DESC
        LIMIT ?
    """
    params.append(limit)
    return await db.fetchall(query, tuple(params))


# ============================================================================
# ID PESAN LEADERBOARD -- dipake buat edit-in-place tiap /settings
# leaderboard_refresh atau refresh terjadwal, bukan kirim pesan baru tiap
# kali. Disimpen lewat tabel `settings` yang sama kayak setting lain
# (leaderboard_channel_id dst), bukan tabel sendiri.
# ============================================================================

async def get_leaderboard_message_id(db: Database) -> int | None:
    value = await settings_q.get_setting(db, "leaderboard_message_id")
    return int(value) if value else None


async def set_leaderboard_message_id(db: Database, message_id: int) -> None:
    await settings_q.set_setting(db, "leaderboard_message_id", str(message_id))


# ============================================================================
# BADGE CUSTOM LEADERBOARD -- CUMA berlaku buat top 1-3 (lihat
# bot.cogs.badge & bot.ui.views.BadgePanelView).
# ============================================================================

async def get_badge(db: Database, user_id: int):
    return await db.fetchone("SELECT * FROM leaderboard_badges WHERE user_id = ?", (user_id,))


async def set_badge(db: Database, user_id: int, text: str, color_from: str, color_to: str) -> None:
    await db.execute(
        """
        INSERT INTO leaderboard_badges (user_id, text, color_from, color_to, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        ON CONFLICT(user_id) DO UPDATE SET
            text = excluded.text,
            color_from = excluded.color_from,
            color_to = excluded.color_to,
            updated_at = datetime('now')
        """,
        (user_id, text, color_from, color_to),
    )


async def delete_badge(db: Database, user_id: int) -> bool:
    """Return True kalau ada row yang beneran kehapus (dipake caller buat
    ngasih tau user kalau dia emang belum punya badge yang keset)."""
    row = await get_badge(db, user_id)
    if row is None:
        return False
    await db.execute("DELETE FROM leaderboard_badges WHERE user_id = ?", (user_id,))
    return True


async def list_badges(db: Database):
    return await db.fetchall("SELECT * FROM leaderboard_badges ORDER BY updated_at DESC")
