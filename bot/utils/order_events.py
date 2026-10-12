"""Pencatat jejak aksi staff per order (bukti bayar masuk, lunas, selesai,
batal, refund) -- dipanggil dari bot.utils.order_actions. Data ini dipake
SLA (bot.cogs.sla) dan statistik staff (bot.cogs.staff_stats).

BEST-EFFORT: gagal nyatet gak boleh ngeganggu aksi order-nya sendiri, jadi
semua error ditelan dan cuma dicatat di log."""

from __future__ import annotations

import discord

from bot.core.logger import logger
from bot.database.queries import staff_metrics as metrics_q

KINDS = ("proof", "paid", "completed", "cancelled", "refunded")


async def record(db, order_id: int, kind: str, actor: discord.abc.User | None = None) -> None:
    """`actor` None = aksi otomatis/bukan staff (misal bukti bayar dari
    customer, atau order lunas via Kartu) -- gak masuk statistik staff."""
    try:
        await metrics_q.record_order_event(db, order_id, kind, actor.id if actor is not None else None)
    except Exception:  # noqa: BLE001
        logger.warning("Gagal nyatet event '%s' buat order #%s.", kind, order_id)
