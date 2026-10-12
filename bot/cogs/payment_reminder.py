"""
Pengingat batas bayar: DM ke pembeli X menit sebelum order yang belum dibayar
kedaluwarsa otomatis (pembatalannya sendiri tetep ditangani bot.cogs.tasks).

  * Cuma order yang BELUM ngirim bukti bayar sama sekali.
  * Tiap order cuma diingetin SEKALI (tercatat di tabel pay_reminders).
  * Jarak pengingat diatur lewat /sla bayar (default 10 menit, 0 = mati).
  * Order yang baru dibuat <2 menit diabaikan, biar batas bayar yang pendek
    gak langsung bikin pengingat sesaat setelah checkout.
"""

from __future__ import annotations

import discord
from discord.ext import commands, tasks

from bot.core.logger import logger
from bot.database.queries import staff_metrics as metrics_q
from bot.ui import components
from bot.utils import sla as sla_util


class PaymentReminderCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.remind_loop.start()

    async def cog_unload(self) -> None:
        self.remind_loop.cancel()

    @tasks.loop(minutes=1)
    async def remind_loop(self) -> None:
        try:
            db = self.bot.db
            cfg = await sla_util.load_config(db)
            if cfg.pay_reminder_minutes <= 0:
                return
            for row in await metrics_q.list_orders_near_payment_deadline(db, cfg.pay_reminder_minutes):
                # Tandain DULUAN: kalau DM gagal/error, gak diulang tiap menit.
                await metrics_q.mark_pay_reminded(db, row["id"])
                deadline = sla_util.parse_sqlite_utc(row["payment_deadline"])
                if deadline is None:
                    continue
                card = {
                    "order_id": row["id"], "amount": float(row["total_price"]),
                    "currency": row["currency_label"], "deadline_ts": int(deadline.timestamp()),
                }
                user = self.bot.get_user(row["user_id"])
                if user is None:
                    try:
                        user = await self.bot.fetch_user(row["user_id"])
                    except discord.HTTPException:
                        continue
                try:
                    await user.send(view=components.NoctraLayout(components.pay_reminder_container(card), timeout=None))
                except discord.HTTPException:
                    pass  # DM ditutup -- gak masalah
        except Exception:  # noqa: BLE001
            logger.exception("Error di pengingat batas bayar.")

    @remind_loop.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PaymentReminderCog(bot))
