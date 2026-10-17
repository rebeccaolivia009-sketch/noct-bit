"""Query helpers buat kartu Statistik Pembelian (/stats): info tambahan
pembeli dan riwayat pesanan. Aturan "pesanan yang kehitung" SAMA dengan
leaderboard: status = completed DAN payment_status = paid."""

from __future__ import annotations

from bot.database.core import Database

_COUNTED = "status = 'completed' AND payment_status = 'paid'"


async def get_extras(db: Database, user_id: int) -> dict:
    """Pesanan pertama/terakhir, pesanan terbesar, dan produk paling
    sering dibeli (dihitung per qty, seri -> yang terakhir dibeli)."""
    row = await db.fetchone(
        f"SELECT MIN(created_at) AS first_at, MAX(created_at) AS last_at, MAX(total_price) AS biggest "
        f"FROM orders WHERE user_id = ? AND {_COUNTED}",
        (user_id,),
    )
    favorite = await db.fetchone(
        """
        SELECT oi.product_name AS name, SUM(oi.quantity) AS qty
        FROM order_items oi JOIN orders o ON o.id = oi.order_id
        WHERE o.user_id = ? AND o.status = 'completed' AND o.payment_status = 'paid'
        GROUP BY oi.product_name
        ORDER BY qty DESC, MAX(o.created_at) DESC
        LIMIT 1
        """,
        (user_id,),
    )
    return {
        "first_at": row["first_at"] if row else None,
        "last_at": row["last_at"] if row else None,
        "biggest": float(row["biggest"] or 0) if row else 0.0,
        "favorite_name": favorite["name"] if favorite else None,
        "favorite_qty": int(favorite["qty"]) if favorite else 0,
    }


async def count_orders(db: Database, user_id: int) -> int:
    row = await db.fetchone(f"SELECT COUNT(*) AS n FROM orders WHERE user_id = ? AND {_COUNTED}", (user_id,))
    return int(row["n"]) if row else 0


async def list_orders(db: Database, user_id: int, limit: int, offset: int = 0):
    """Pesanan yang kehitung, terbaru dulu."""
    return await db.fetchall(
        f"SELECT * FROM orders WHERE user_id = ? AND {_COUNTED} "
        f"ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    )
