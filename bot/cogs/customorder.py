"""Command admin: /customorder -- panel setting "Produk Lainnya" (order
produk di luar katalog). Lihat bot.utils.custom_order dan bot.ui.customorder."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.ui.customorder import CustomOrderPanel
from bot.ui.quotes import QUOTE_ITEMS
from bot.utils import custom_order
from bot.utils.permissions import staff_only


class CustomOrderCog(commands.Cog):
    """Atur fitur Produk Lainnya."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # Tombol penawaran (Set Harga / Tolak / Lanjut Bayar / Batalkan) persisten.
        self.bot.add_dynamic_items(*QUOTE_ITEMS)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(*QUOTE_ITEMS)

    @app_commands.command(
        name="customorder",
        description="Atur 'Produk Lainnya': order produk di luar katalog (form, harga, tombol di panel order).",
    )
    @app_commands.guild_only()
    @staff_only()
    async def customorder(self, interaction: discord.Interaction) -> None:
        panel = CustomOrderPanel(interaction.user.id, await custom_order.load(self.bot.db))
        await interaction.response.send_message(view=await panel.render(self.bot), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CustomOrderCog(bot))
