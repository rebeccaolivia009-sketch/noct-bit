"""Command admin: /customorder -- panel setting "Produk Lainnya" (order
produk di luar katalog). Lihat bot.utils.custom_order dan bot.ui.customorder."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.ui.customorder import CustomOrderPanel
from bot.utils import custom_order
from bot.utils.permissions import staff_only


class CustomOrderCog(commands.Cog):
    """Atur fitur Produk Lainnya."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

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
