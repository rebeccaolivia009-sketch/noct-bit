"""
Task background: refresh footer "Terakhir update" panel toko
(/settings shop_panel) tiap 10 detik -- biar panel keliatan "live" tanpa
staff perlu posting ulang manual. State-nya (channel/message id + isi
panel) disimpen di shop_panel_state (lihat
bot.database.queries.shop_panel), jadi tetep jalan abis bot restart --
gak butuh apa-apa dari memori proses sebelumnya.

CATATAN RATE LIMIT: edit tiap 10 detik itu cukup sering -- Discord ngasih
budget rate limit per-channel yang kebagi sama semua aktivitas bot di
channel itu (pesan lain, command lain, dll). Kalau nanti kerasa kena
rate limit di channel yang ramai, naikin interval @tasks.loop di bawah
(misal ke 30-60 detik) daripada dipaksa 10 detik terus -- satu-satunya
tempat yang perlu diubah cuma angka di decorator-nya.
"""

from __future__ import annotations

import discord
from discord.ext import commands, tasks

from bot.core.logger import logger
from bot.database.queries import shop_panel as shop_panel_q
from bot.ui.views import ShopPanelView


class ShopPanelTaskCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.refresh_loop.start()

    async def cog_unload(self) -> None:
        self.refresh_loop.cancel()

    @tasks.loop(seconds=10)
    async def refresh_loop(self) -> None:
        rows = await shop_panel_q.list_all(self.bot.db)
        for row in rows:
            channel = self.bot.get_channel(row["channel_id"])
            if not isinstance(channel, discord.TextChannel):
                continue
            try:
                message = await channel.fetch_message(row["message_id"])
            except discord.NotFound:
                continue  # panel-nya kehapus manual -- biarin, staff yang harus posting ulang
            except discord.HTTPException:
                logger.exception("Gagal fetch pesan panel toko guild %s.", row["guild_id"])
                continue

            view = ShopPanelView(
                title=row["title"],
                description=row["description"],
                banner_url=row["banner_url"],
                thumbnail_url=row["thumbnail_url"],
                button_label=row["button_label"],
                button_emoji=row["button_emoji"],
            )
            try:
                await message.edit(view=view)
            except discord.HTTPException:
                logger.exception("Gagal refresh panel toko di guild %s.", row["guild_id"])

    @refresh_loop.before_loop
    async def before_refresh_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ShopPanelTaskCog(bot))
