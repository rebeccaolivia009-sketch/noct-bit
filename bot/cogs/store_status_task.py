"""
Task background: ngecek jam operasional toko tiap 60 detik, dan CUMA
refresh panel status kalau hasil hitungannya BENERAN beda dari state yang
kesimpen terakhir (bukan edit pesan tiap tick -- toko cuma ganti status
2x sehari, gak ada gunanya nge-hit Discord API tiap menit kalau gak ada
yang berubah).

Ini yang bikin toko buka/tutup OTOMATIS sesuai jam operasional
(/storestatus jam_operasional) -- staff GAK toggle manual lagi.
"""

from __future__ import annotations

from discord.ext import commands, tasks

from bot.core.logger import logger
from bot.utils.helpers import RuntimeSettings
from bot.utils.store_status import compute_state, notify_state_ping, refresh_store_status


class StoreStatusTaskCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.check_loop.start()

    async def cog_unload(self) -> None:
        self.check_loop.cancel()

    @tasks.loop(seconds=60)
    async def check_loop(self) -> None:
        runtime = RuntimeSettings(self.bot.db)
        open_time = await runtime.store_status_open_time()
        close_time = await runtime.store_status_close_time()
        new_state = compute_state(open_time, close_time)
        cached_state = await runtime.store_status_state()

        if new_state != cached_state:
            ok = await refresh_store_status(self.bot)
            if ok:
                await notify_state_ping(self.bot, new_state)
            logger.info("Status toko otomatis berubah jadi %s (jam operasional %s-%s WIB).",
                        new_state, open_time, close_time)

    @check_loop.before_loop
    async def before_check_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StoreStatusTaskCog(bot))
