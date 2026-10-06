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


async def get_spender_profile(
    db: Database, user_id: int, excluded_user_ids: list[int] | None = None
) -> dict | None:
    """Profil belanja SATU user buat notifikasi "Pembelian Baru": total
    belanja, jumlah order, peringkat, dan selisih ke peringkat di atasnya.

    Aturan hitungnya SAMA PERSIS kayak get_top_spenders() di atas (cuma
    order completed + paid, akun di `excluded_user_ids` gak ikut keitung),
    jadi angka & peringkatnya selalu nyambung sama papan leaderboard.

    Peringkat = jumlah user yang total belanjanya LEBIH BESAR + 1, jadi dua
    user dengan total sama dapet peringkat yang sama.

    Return None kalau user itu dikecualiin dari leaderboard, atau belum
    punya satu pun order completed+paid -- PENTING: panggil ini SETELAH
    order yang barusan selesai udah tersimpen sebagai completed+paid,
    kalau enggak order itu belum kehitung di total/peringkatnya."""
    excluded_user_ids = excluded_user_ids or []
    if user_id in excluded_user_ids:
        return None

    params: list = []
    exclude_clause = ""
    if excluded_user_ids:
        placeholders = ",".join("?" for _ in excluded_user_ids)
        exclude_clause = f"AND user_id NOT IN ({placeholders})"
        params.extend(excluded_user_ids)

    query = f"""
        WITH spend AS (
            SELECT user_id, SUM(total_price) AS total_spent, COUNT(*) AS total_orders
            FROM orders
            WHERE status = 'completed' AND payment_status = 'paid'
            {exclude_clause}
            GROUP BY user_id
        ),
        me AS (SELECT total_spent, total_orders FROM spend WHERE user_id = ?)
        SELECT
            me.total_spent AS total_spent,
            me.total_orders AS total_orders,
            (SELECT COUNT(*) FROM spend WHERE total_spent > me.total_spent) + 1 AS rank,
            (SELECT COUNT(*) FROM spend) AS total_spenders,
            (SELECT MIN(total_spent) FROM spend WHERE total_spent > me.total_spent) AS next_total
        FROM me
    """
    params.append(user_id)
    row = await db.fetchone(query, tuple(params))
    if row is None or row["total_spent"] is None:
        return None

    next_total = row["next_total"]
    return {
        "total_spent": float(row["total_spent"]),
        "total_orders": int(row["total_orders"]),
        "rank": int(row["rank"]),
        "total_spenders": int(row["total_spenders"]),
        "gap_to_next": round(float(next_total) - float(row["total_spent"]), 2) if next_total is not None else None,
    }


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
