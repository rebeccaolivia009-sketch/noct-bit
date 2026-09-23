"""Command admin: /product (create/edit/delete/list/visibility).

Produk sekarang punya Category Type (Category -> Category Type -> Product)
bukan langsung Category -- dan dynamic checkout field ada di Category Type,
lihat bot.cogs.category_type buat dua-duanya.
"""

from __future__ import annotations

from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import category_types as category_types_q
from bot.database.queries import products as products_q
from bot.ui import embeds
from bot.utils.autocomplete import category_type_autocomplete, product_autocomplete
from bot.utils.helpers import RuntimeSettings
from bot.utils.permissions import staff_only
from bot.utils.validators import is_valid_emoji

ProductType = Literal["manual", "automatic", "digital", "service"]
StockType = Literal["unlimited", "manual"]
DiscountType = Literal["none", "percent", "flat"]


class ProductCog(commands.Cog):
    """Kelola katalog produk."""

    product_group = app_commands.Group(
        name="product", description="Kelola produk toko.", guild_only=True
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- CRUD Produk --------------------------------------------------------

    @product_group.command(name="create", description="Bikin produk baru.")
    @app_commands.describe(
        category_type="Category type buat produk ini",
        name="Nama produk",
        product_type="Tipe pengiriman",
        stock_type="Stok unlimited atau dicatat manual",
        base_price="Harga dasar (sebelum diskon)",
        stock_quantity="Stok awal (cuma dipake kalau stock_type=manual)",
        currency_label="Label mata uang, misal USD, IDR, Robux",
        description="Deskripsi produk",
        image_url="URL gambar banner/thumbnail (PNG/JPG/WebP)",
        emoji="Emoji opsional yang muncul di samping produk ini pas /shop",
    )
    @app_commands.autocomplete(category_type=category_type_autocomplete)
    @staff_only()
    async def create(
        self,
        interaction: discord.Interaction,
        category_type: int,
        name: str,
        product_type: ProductType,
        stock_type: StockType,
        base_price: app_commands.Range[float, 0, None],
        stock_quantity: app_commands.Range[int, 0, None] = 0,
        currency_label: str | None = None,
        description: str | None = None,
        image_url: str | None = None,
        emoji: str | None = None,
    ) -> None:
        if not await category_types_q.get_category_type(self.bot.db, category_type):
            await interaction.response.send_message(embed=embeds.error_embed("Category type gak ketemu."), ephemeral=True)
            return
        if emoji and not is_valid_emoji(emoji):
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Itu kayaknya bukan emoji yang valid. Pake emoji biasa atau custom emoji dari server ini."
                ),
                ephemeral=True,
            )
            return
        currency = currency_label or await RuntimeSettings(self.bot.db).default_currency()
        product_id = await products_q.create_product(
            self.bot.db, category_type, name, description, product_type, stock_type,
            stock_quantity, base_price, currency, image_url, emoji,
        )
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Produk **{name}** berhasil dibuat dengan ID `{product_id}`."),
            ephemeral=True,
        )

    @product_group.command(name="edit", description="Edit produk yang udah ada.")
    @app_commands.describe(
        product="Produk yang mau diedit",
        name="Nama baru",
        category_type="Pindah ke category type yang lain",
        description="Deskripsi baru",
        image_url="URL banner/thumbnail baru",
        emoji="Emoji baru (ketik none buat hapus)",
        product_type="Tipe pengiriman baru",
        stock_type="Tipe stok baru",
        stock_quantity="Jumlah stok baru (khusus stok manual)",
        base_price="Harga dasar baru",
        currency_label="Label mata uang baru",
        discount_type="Tipe diskon, atau none buat hapus",
        discount_value="Nilai diskon (angka persen atau nominal flat)",
    )
    @app_commands.autocomplete(product=product_autocomplete, category_type=category_type_autocomplete)
    @staff_only()
    async def edit(
        self,
        interaction: discord.Interaction,
        product: int,
        name: str | None = None,
        category_type: int | None = None,
        description: str | None = None,
        image_url: str | None = None,
        emoji: str | None = None,
        product_type: ProductType | None = None,
        stock_type: StockType | None = None,
        stock_quantity: int | None = None,
        base_price: float | None = None,
        currency_label: str | None = None,
        discount_type: DiscountType | None = None,
        discount_value: float | None = None,
    ) -> None:
        existing = await products_q.get_product(self.bot.db, product)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Produk gak ketemu."), ephemeral=True)
            return
        if emoji and emoji != "none" and not is_valid_emoji(emoji):
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Itu kayaknya bukan emoji yang valid. Pake emoji biasa atau custom emoji dari server ini."
                ),
                ephemeral=True,
            )
            return

        updates = {}
        if name is not None:
            updates["name"] = name
        if category_type is not None:
            updates["category_type_id"] = category_type
        if description is not None:
            updates["description"] = description
        if image_url is not None:
            updates["image_url"] = image_url
        if emoji is not None:
            updates["emoji"] = None if emoji == "none" else emoji
        if product_type is not None:
            updates["product_type"] = product_type
        if stock_type is not None:
            updates["stock_type"] = stock_type
        if stock_quantity is not None:
            updates["stock_quantity"] = stock_quantity
        if base_price is not None:
            updates["base_price"] = base_price
        if currency_label is not None:
            updates["currency_label"] = currency_label
        if discount_type is not None:
            updates["discount_type"] = None if discount_type == "none" else discount_type
        if discount_value is not None:
            updates["discount_value"] = discount_value

        await products_q.update_product(self.bot.db, product, **updates)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Produk `#{product}` berhasil diupdate."), ephemeral=True
        )

    @product_group.command(name="delete", description="Hapus produk.")
    @app_commands.describe(product="Produk yang mau dihapus")
    @app_commands.autocomplete(product=product_autocomplete)
    @staff_only()
    async def delete(self, interaction: discord.Interaction, product: int) -> None:
        existing = await products_q.get_product(self.bot.db, product)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Produk gak ketemu."), ephemeral=True)
            return
        await products_q.delete_product(self.bot.db, product)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Produk **{existing['name']}** udah dihapus."), ephemeral=True
        )

    @product_group.command(name="visibility", description="Tampilin atau sembunyiin produk di /shop.")
    @app_commands.describe(product="Produk yang mau diatur", visible="True buat tampilin, False buat sembunyiin")
    @app_commands.autocomplete(product=product_autocomplete)
    @staff_only()
    async def visibility(self, interaction: discord.Interaction, product: int, visible: bool) -> None:
        await products_q.set_product_visible(self.bot.db, product, visible)
        state = "kelihatan" if visible else "disembunyiin"
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Produk `#{product}` sekarang **{state}**."), ephemeral=True
        )

    @product_group.command(name="list", description="Liat produk, bisa difilter per category type.")
    @app_commands.describe(category_type="Filter berdasarkan category type")
    @app_commands.autocomplete(category_type=category_type_autocomplete)
    @staff_only()
    async def list_products(self, interaction: discord.Interaction, category_type: int | None = None) -> None:
        type_row = await category_types_q.get_category_type(self.bot.db, category_type) if category_type else None
        rows = await products_q.list_products(self.bot.db, category_type_id=category_type)
        await interaction.response.send_message(
            embed=embeds.product_list_embed(type_row, rows), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProductCog(bot))
