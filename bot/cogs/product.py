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
from bot.database.queries import product_groups as product_groups_q
from bot.database.queries import products as products_q
from bot.ui import embeds
from bot.utils.autocomplete import (
    NO_GROUP_VALUE,
    category_type_autocomplete,
    product_autocomplete,
    product_group_autocomplete,
)
from bot.utils import delete_guard
from bot.utils.helpers import RuntimeSettings
from bot.utils.permissions import staff_only
from bot.utils.validators import is_valid_emoji

ProductType = Literal["manual", "automatic", "digital", "service"]
StockType = Literal["unlimited", "manual"]
DiscountType = Literal["none", "percent", "flat"]
StockMode = Literal["unit", "amount"]
StockAction = Literal["set", "add", "subtract"]

_BAD_EMOJI = "Itu kayaknya bukan emoji yang valid. Pake emoji biasa atau custom emoji dari server ini."


async def _check_group(db, category_type_id: int, product_group_id: int | None):
    """Validasi Product Type buat produk di `category_type_id`. Return
    (group_row_atau_None, pesan_error_atau_None)."""
    if not product_group_id:
        return None, None
    group = await product_groups_q.get_product_group(db, product_group_id)
    if group is None:
        return None, "Product Type gak ketemu."
    if group["category_type_id"] != category_type_id:
        return None, "Product Type itu bukan bagian dari category type produk ini."
    return group, None


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
        product_group="Product Type (opsional) -- kosongin kalau gak perlu",
        stock_mode="Tipe stok: unit = satuan (1 per qty), amount = jumlah (misal Robux)",
        stock_cost="Mode amount: stok yang kepake per 1 qty (misal 800 buat paket 800 Robux)",
        stock_label="Satuan stok buat tampilan, misal Robux (opsional)",
    )
    @app_commands.autocomplete(
        category_type=category_type_autocomplete, product_group=product_group_autocomplete
    )
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
        product_group: int | None = None,
        stock_mode: StockMode = "unit",
        stock_cost: app_commands.Range[int, 1, None] = 1,
        stock_label: app_commands.Range[str, 1, 20] | None = None,
    ) -> None:
        db = self.bot.db
        if not await category_types_q.get_category_type(db, category_type):
            await interaction.response.send_message(embed=embeds.error_embed("Category type gak ketemu."), ephemeral=True)
            return
        if emoji and not is_valid_emoji(emoji):
            await interaction.response.send_message(embed=embeds.error_embed(_BAD_EMOJI), ephemeral=True)
            return
        if product_group == NO_GROUP_VALUE:
            product_group = None
        group, error = await _check_group(db, category_type, product_group)
        if error:
            await interaction.response.send_message(embed=embeds.error_embed(error), ephemeral=True)
            return
        pooled = bool(group and group["shared_stock"])
        if stock_mode == "amount" and stock_type != "manual" and not pooled:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Tipe stok `amount` butuh `stock_type: manual` (atau Product Type dengan stok bersama) -- "
                    "stok unlimited gak ada yang dihitung."
                ),
                ephemeral=True,
            )
            return

        currency = currency_label or await RuntimeSettings(db).default_currency()
        product_id = await products_q.create_product(
            db, category_type, name, description, product_type, stock_type,
            stock_quantity, base_price, currency, image_url, emoji,
            product_group_id=product_group, stock_mode=stock_mode,
            stock_cost=stock_cost, stock_label=stock_label,
        )
        note = ""
        if pooled:
            note = (
                f"\nProduk ini narik dari pool stok bersama **{group['name']}** "
                f"(`stock_type`/`stock_quantity` produk diabaikan)."
            )
            if group["stock_mode"] == "amount":
                note += f" Tiap qty motong **{stock_cost:,}** dari pool."
        elif stock_mode == "amount":
            note = f"\nStok tipe jumlah: tiap qty motong **{stock_cost:,}** dari stok produk ini."
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Produk **{name}** berhasil dibuat dengan ID `{product_id}`.{note}"),
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
        product_group="Product Type baru (pilih \"Tanpa Product Type\" buat ngelepas)",
        stock_mode="Tipe stok baru: unit (satuan) / amount (jumlah)",
        stock_cost="Mode amount: stok yang kepake per 1 qty",
        stock_label="Satuan stok baru (ketik none buat hapus)",
    )
    @app_commands.autocomplete(
        product=product_autocomplete,
        category_type=category_type_autocomplete,
        product_group=product_group_autocomplete,
    )
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
        product_group: int | None = None,
        stock_mode: StockMode | None = None,
        stock_cost: app_commands.Range[int, 1, None] | None = None,
        stock_label: str | None = None,
    ) -> None:
        db = self.bot.db
        existing = await products_q.get_product(db, product)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Produk gak ketemu."), ephemeral=True)
            return
        if emoji and emoji != "none" and not is_valid_emoji(emoji):
            await interaction.response.send_message(embed=embeds.error_embed(_BAD_EMOJI), ephemeral=True)
            return

        # Product Type: harus bagian dari category type (baru) produk ini.
        # Kalau produk dipindah ke category type lain TANPA milih Product
        # Type baru, Product Type lamanya otomatis dilepas (itu punya tipe
        # yang lama, gak valid di tipe yang baru).
        target_type = category_type if category_type is not None else existing["category_type_id"]
        new_group_id = existing["product_group_id"]
        group_changed = False
        if product_group is not None:
            group_changed = True
            new_group_id = None if product_group == NO_GROUP_VALUE else product_group
        elif category_type is not None and category_type != existing["category_type_id"] and new_group_id:
            group_changed, new_group_id = True, None
        if group_changed:
            _, error = await _check_group(db, target_type, new_group_id)
            if error:
                await interaction.response.send_message(embed=embeds.error_embed(error), ephemeral=True)
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
        if group_changed:
            updates["product_group_id"] = new_group_id
        if stock_mode is not None:
            updates["stock_mode"] = stock_mode
        if stock_cost is not None:
            updates["stock_cost"] = stock_cost
        if stock_label is not None:
            updates["stock_label"] = None if stock_label.lower() == "none" else stock_label[:20]

        final_mode = updates.get("stock_mode", existing["stock_mode"])
        final_type = updates.get("stock_type", existing["stock_type"])
        final_group = await product_groups_q.get_product_group(db, new_group_id) if new_group_id else None
        if final_mode == "amount" and final_type != "manual" and not (final_group and final_group["shared_stock"]):
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Tipe stok `amount` butuh `stock_type: manual` (atau Product Type dengan stok bersama)."
                ),
                ephemeral=True,
            )
            return

        await products_q.update_product(db, product, **updates)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Produk `#{product}` berhasil diupdate."), ephemeral=True
        )

    @product_group.command(name="stock", description="Isi ulang / kurangi / set stok produk.")
    @app_commands.describe(
        product="Produk yang stoknya mau diatur",
        action="set = timpa angka, add = tambah (restock), subtract = kurangi",
        amount="Jumlahnya (dalam satuan stok produk ini)",
    )
    @app_commands.autocomplete(product=product_autocomplete)
    @staff_only()
    async def stock(
        self,
        interaction: discord.Interaction,
        product: int,
        action: StockAction,
        amount: app_commands.Range[int, 0, None],
    ) -> None:
        db = self.bot.db
        existing = await products_q.get_product(db, product)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Produk gak ketemu."), ephemeral=True)
            return
        group = (
            await product_groups_q.get_product_group(db, existing["product_group_id"])
            if existing["product_group_id"] else None
        )
        if group and group["shared_stock"]:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"Produk ini narik dari pool bersama **{group['name']}**. "
                    "Atur stoknya lewat `/product_group stock`."
                ),
                ephemeral=True,
            )
            return
        if existing["stock_type"] != "manual":
            await interaction.response.send_message(
                embed=embeds.error_embed("Produk ini stoknya unlimited. Ubah dulu `stock_type` ke manual lewat `/product edit`."),
                ephemeral=True,
            )
            return
        current = int(existing["stock_quantity"])
        new_value = {"set": amount, "add": current + amount, "subtract": max(0, current - amount)}[action]
        await products_q.set_stock(db, product, new_value)
        unit = f" {existing['stock_label']}" if existing["stock_label"] else ""
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Stok **{existing['name']}**: {current:,}{unit} -> **{new_value:,}{unit}**."),
            ephemeral=True,
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
        blocked = await delete_guard.blocked_message(
            self.bot.db,
            what=f"Produk **{existing['name']}**",
            hide_hint=f"Pakai `/product visibility product:{product} visible:False` buat nyembunyiin dari /shop (riwayat tetap aman).",
            product_id=product,
        )
        if blocked:
            await interaction.response.send_message(embed=embeds.error_embed(blocked), ephemeral=True)
            return
        try:
            await products_q.delete_product(self.bot.db, product)
        except Exception as exc:  # noqa: BLE001
            if not delete_guard.is_fk_error(exc):
                raise
            await interaction.response.send_message(
                embed=embeds.error_embed(delete_guard.FALLBACK_MESSAGE), ephemeral=True
            )
            return
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
        db = self.bot.db
        type_row = await category_types_q.get_category_type(db, category_type) if category_type else None
        rows = await products_q.list_products(db, category_type_id=category_type)
        groups = await product_groups_q.list_product_groups(db, category_type_id=category_type)
        await interaction.response.send_message(
            embed=embeds.product_admin_list_embed(type_row, rows, {g["id"]: g for g in groups}),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProductCog(bot))
