"""Command admin: /builder -- buka panel Catalog Builder (lihat bot.ui.builder)."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.ui.builder import Builder
from bot.utils.permissions import staff_only


class BuilderCog(commands.Cog):
    """Satu panel buat ngatur semua katalog: kategori sampai field checkout."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="builder",
        description="Buka panel Catalog Builder: atur kategori, tipe, produk, stok, dan field checkout.",
    )
    @app_commands.guild_only()
    @staff_only()
    async def builder(self, interaction: discord.Interaction) -> None:
        panel = Builder(interaction.user.id)
        view = await panel.render(self.bot.db)
        await interaction.response.send_message(view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BuilderCog(bot))
