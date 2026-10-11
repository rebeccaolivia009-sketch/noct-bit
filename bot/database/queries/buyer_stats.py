"""Query read-only buat riwayat belanja SATU pembeli -- dipake /buyerinfo
(status kepercayaan), tier loyalitas, dan checker lain. Aturan "belanja
beneran" SAMA kayak leaderboard (bot.database.queries.leaderboard): cuma
order status='completed' DAN payment_status='paid'."""

from __future__ import annotations

from bot.database.core import Database


async def get_buyer_stats(db: Database, user_id: int) -> dict:
    row = await db.fetchone(
        """
        SELECT
            COUNT(*) AS total_orders,
            COALESCE(SUM(CASE WHEN status = 'completed' AND payment_status = 'paid' THEN 1 ELSE 0 END), 0) AS completed,
            COALESCE(SUM(CASE WHEN status = 'completed' AND payment_status = 'paid' THEN total_price ELSE 0 END), 0) AS total_spent,
            COALESCE(SUM(CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END), 0) AS cancelled,
            COALESCE(SUM(CASE WHEN status = 'refunded' THEN 1 ELSE 0 END), 0) AS refunded,
            COALESCE(SUM(CASE WHEN payment_status = 'expired' THEN 1 ELSE 0 END), 0) AS expired,
            MIN(created_at) AS first_order_at,
            MAX(created_at) AS last_order_at
        FROM orders WHERE user_id = ?
        """,
        (user_id,),
    )
    currency_row = await db.fetchone(
        "SELECT currency_label FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
    )
    return {
        "total_orders": int(row["total_orders"]),
        "completed": int(row["completed"]),
        "total_spent": float(row["total_spent"]),
        "cancelled": int(row["cancelled"]),
        "refunded": int(row["refunded"]),
        "expired": int(row["expired"]),
        "first_order_at": row["first_order_at"],
        "last_order_at": row["last_order_at"],
        "currency_label": currency_row["currency_label"] if currency_row else None,
    }


async def get_all_spenders(db: Database, excluded_user_ids: list[int] | None = None):
    """Semua (user_id, total_spent) yang punya belanja beneran > 0 -- buat
    sinkronisasi tier massal. `excluded_user_ids` (akun staff/tester dari
    /settings leaderboard_exclude) gak ikut."""
    excluded_user_ids = excluded_user_ids or []
    params: list = []
    clause = ""
    if excluded_user_ids:
        clause = f"AND user_id NOT IN ({','.join('?' for _ in excluded_user_ids)})"
        params.extend(excluded_user_ids)
    rows = await db.fetchall(
        f"""
        SELECT user_id, SUM(total_price) AS total_spent FROM orders
        WHERE status = 'completed' AND payment_status = 'paid' {clause}
        GROUP BY user_id HAVING total_spent > 0 ORDER BY total_spent DESC
        """,
        tuple(params),
    )
    return [(int(r["user_id"]), float(r["total_spent"])) for r in rows]


async def get_store_currency(db: Database) -> str | None:
    """Mata uang order TERAKHIR di toko -- buat nampilin ambang tier."""
    row = await db.fetchone("SELECT currency_label FROM orders ORDER BY id DESC LIMIT 1")
    return row["currency_label"] if row else None
