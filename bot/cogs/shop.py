"""Command user: /shop, /buy"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.core.theme import COLOR_ACCENT
from bot.database.queries import categories as categories_q
from bot.database.queries import products as products_q
from bot.ui import embeds
from bot.ui.views import CategoryBrowseView, start_purchase


async def _visible_product_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    db = interaction.client.db  # type: ignore[attr-defined]
    rows = await products_q.search_products(db, current, limit=25, visible_only=True)
    return [app_commands.Choice(name=f"{r['name']}", value=r["id"]) for r in rows]


class ShopCog(commands.Cog):
    """Jelajahin katalog dan beli produk."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="shop", description="Jelajahin toko NOCTRA.")
    @app_commands.guild_only()
    async def shop(self, interaction: discord.Interaction) -> None:
        categories = await categories_q.list_categories(self.bot.db, enabled_only=True)
        embed = embeds.base_embed(
            "NOCTRA STORE",
            "Pilih kategori di bawah buat liat produk yang ada.",
            color=COLOR_ACCENT,
        )
        if not categories:
            embed.description = "Toko belum ada kategori yang aktif nih. Cek lagi nanti ya."
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        await interaction.response.send_message(
            embed=embed, view=CategoryBrowseView(categories), ephemeral=True
        )

    @app_commands.command(name="buy", description="Beli produk langsung.")
    @app_commands.describe(product="Produk yang mau kamu beli")
    @app_commands.autocomplete(product=_visible_product_autocomplete)
    @app_commands.guild_only()
    async def buy(self, interaction: discord.Interaction, product: int) -> None:
        await start_purchase(interaction, product)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ShopCog(bot))
