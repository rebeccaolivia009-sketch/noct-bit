"""
Background loop:
  * Expire order yang deadline bayarnya udah lewat.
  * Auto-archive ticket yang udah inaktif lewat batas waktu yang diatur.
  * Bersihin flag awaiting-photo review yang basi (customer gak pernah
    kirim foto dan gak klik Lewati -- dibersihin abis PHOTO_WINDOW_MINUTES).
  * Akhirin giveaway otomatis begitu waktunya abis (pilih pemenang, kasih
    win-role, update kartu jadi status berakhir).
"""

from __future__ import annotations

import discord
from discord.ext import commands, tasks

from bot.core.logger import logger
from bot.database.queries import giveaways as giveaways_q
from bot.database.queries import orders as orders_q
from bot.database.queries import products as products_q
from bot.database.queries import reviews as reviews_q
from bot.database.queries import tickets as tickets_q
from bot.ui import embeds
from bot.utils import giveaway_actions, ticket_actions
from bot.utils.helpers import RuntimeSettings


class TasksCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.expire_payments.start()
        self.auto_archive_tickets.start()
        self.clear_stale_photo_windows.start()
        self.end_expired_giveaways.start()

    def cog_unload(self) -> None:
        self.expire_payments.cancel()
        self.auto_archive_tickets.cancel()
        self.clear_stale_photo_windows.cancel()
        self.end_expired_giveaways.cancel()

    @tasks.loop(minutes=2)
    async def expire_payments(self) -> None:
        try:
            db = self.bot.db
            expired = await orders_q.list_expired_pending_payments(db)
            for order in expired:
                await orders_q.set_payment_status(db, order["id"], "expired")
                if order["stock_reserved"]:
                    await products_q.adjust_stock(db, order["product_id"], 1)
                    await orders_q.clear_stock_reserved(db, order["id"])
                try:
                    user = self.bot.get_user(order["user_id"]) or await self.bot.fetch_user(order["user_id"])
                    await user.send(
                        embed=embeds.error_embed(
                            f"Waktu bayar order #{order['id']} kamu udah abis. "
                            "Pesen lagi dari toko kalau masih mau barang ini."
                        )
                    )
                except discord.HTTPException:
                    pass
        except Exception:  # noqa: BLE001
            logger.exception("Error di task expire_payments.")

    @tasks.loop(minutes=15)
    async def auto_archive_tickets(self) -> None:
        try:
            db = self.bot.db
            runtime = RuntimeSettings(db)
            hours = await runtime.ticket_auto_archive_hours()
            stale = await tickets_q.list_stale_tickets(db, hours)
            for ticket in stale:
                channel = self.bot.get_channel(ticket["channel_id"])
                if isinstance(channel, discord.TextChannel):
                    await ticket_actions.close_ticket(
                        self.bot, channel, "NOCTRA (auto-archive)",
                        "Otomatis diarsipin karena inaktif.", auto=True,
                    )
        except Exception:  # noqa: BLE001
            logger.exception("Error di task auto_archive_tickets.")

    @tasks.loop(minutes=5)
    async def clear_stale_photo_windows(self) -> None:
        """Hapus flag awaiting_photo di review yang jendela 10 menitnya udah
        lewat -- biar flag-nya gak nyangkut permanen kalau customer gak
        pernah bales atau bot-nya restart di tengah nunggu."""
        try:
            db = self.bot.db
            stale = await reviews_q.list_stale_awaiting_photo_reviews(db)
            for review in stale:
                await reviews_q.set_awaiting_photo(db, review["id"], False)
                logger.debug("Jendela foto basi dibersihin buat review #%s.", review["id"])
        except Exception:  # noqa: BLE001
            logger.exception("Error di task clear_stale_photo_windows.")

    @tasks.loop(minutes=1)
    async def end_expired_giveaways(self) -> None:
        """Cek tiap menit giveaway yang ends_at-nya udah lewat tapi
        statusnya masih 'active', terus akhirin otomatis (pilih pemenang,
        kasih win-role, update kartu jadi status berakhir)."""
        try:
            db = self.bot.db
            expired = await giveaways_q.list_expired_active_giveaways(db)
            for giveaway in expired:
                await giveaway_actions.end_giveaway(self.bot, giveaway["id"])
        except Exception:  # noqa: BLE001
            logger.exception("Error di task end_expired_giveaways.")

    @expire_payments.before_loop
    @auto_archive_tickets.before_loop
    @clear_stale_photo_windows.before_loop
    @end_expired_giveaways.before_loop
    async def _before(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TasksCog(bot))
