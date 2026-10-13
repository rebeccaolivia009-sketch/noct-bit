"""
Command admin: /product_group  ("Product Type" di tampilan customer/staff).

Level OPSIONAL di antara Category Type dan Product (Category -> Category
Type -> Product Type -> Product). Dipake kalau sebagian produk di satu
Category Type perlu dikelompokin lagi, misal Robux: Via Username / Via
Login / Gamepass. Category Type yang gak punya Product Type tetep jalan
persis kayak biasa.

Nama command pake `product_group` (bukan `product_type`) karena `/product
create` udah punya parameter `product_type` buat jenis pengiriman
(manual/automatic/digital/service) -- biar gak ketuker. Yang dilihat
customer tetep dilabelin "tipe produk".

Product Type juga bisa punya dynamic checkout field TAMBAHAN (subgroup
`/product_group field`) -- opsional. Field itu ditanyain SETELAH field
Category Type-nya dan cuma buat produk di Product Type ini; gak pernah
ngegantiin field Category Type.

Product Type juga bisa megang POOL STOK BERSAMA (shared_stock): semua
produk di bawahnya narik dari satu stok yang sama. Cocok buat stok
Robux -- pool 10.000 Robux, tiap paket (100/500/1000) motong sesuai
isinya. Aturan lengkap ada di bot.utils.stock.
"""

from __future__ import annotations

from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import category_types as category_types_q
from bot.database.queries import fields as fields_q
from bot.database.queries import product_groups as product_groups_q
from bot.ui import embeds
from bot.utils.autocomplete import category_type_autocomplete, product_group_autocomplete
from bot.utils.permissions import staff_only
from bot.utils.validators import is_valid_emoji

StockMode = Literal["unit", "amount"]
StockAction = Literal["set", "add", "subtract"]
FieldType = Literal[
    "username", "userid", "login", "email", "password", "serverid", "gameid", "custom"
]
Validation = Literal["none", "numeric", "alpha", "alphanumeric", "email"]

_BAD_EMOJI = "Itu kayaknya bukan emoji yang valid. Pake emoji biasa atau custom emoji dari server ini."


class ProductGroupCog(commands.Cog):
    """Kelola Product Type (opsional) + pool stok bersamanya."""

    group = app_commands.Group(
        name="product_group",
        description="Kelola Product Type (opsional, di antara Category Type dan Product).",
        guild_only=True,
    )

    field_group = app_commands.Group(
        name="field",
        description="Field checkout TAMBAHAN khusus Product Type (opsional).",
        parent=group,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- CRUD ---------------------------------------------------------------

    @group.command(name="create", description="Bikin Product Type baru di bawah Category Type.")
    @app_commands.describe(
        category_type="Category type induk",
        name="Nama Product Type, misal Via Username",
        description="Deskripsi opsional",
        emoji="Emoji opsional yang muncul di samping tipe ini pas /shop",
        shared_stock="Nyalain biar semua produk di tipe ini narik dari SATU pool stok yang sama",
        stock_mode="Tipe stok pool: unit = satuan, amount = jumlah (misal Robux)",
        stock_quantity="Isi awal pool stok (cuma dipake kalau shared_stock nyala)",
        stock_label="Satuan buat tampilan, misal Robux (opsional)",
    )
    @app_commands.autocomplete(category_type=category_type_autocomplete)
    @staff_only()
    async def create(
        self,
        interaction: discord.Interaction,
        category_type: int,
        name: app_commands.Range[str, 1, 100],
        description: str | None = None,
        emoji: str | None = None,
        shared_stock: bool = False,
        stock_mode: StockMode = "unit",
        stock_quantity: app_commands.Range[int, 0, None] = 0,
        stock_label: app_commands.Range[str, 1, 20] | None = None,
    ) -> None:
        if not await category_types_q.get_category_type(self.bot.db, category_type):
            await interaction.response.send_message(embed=embeds.error_embed("Category type gak ketemu."), ephemeral=True)
            return
        if emoji and not is_valid_emoji(emoji):
            await interaction.response.send_message(embed=embeds.error_embed(_BAD_EMOJI), ephemeral=True)
            return
        group_id = await product_groups_q.create_product_group(
            self.bot.db, category_type, name, description, emoji,
            shared_stock=shared_stock, stock_mode=stock_mode,
            stock_quantity=stock_quantity if shared_stock else 0, stock_label=stock_label,
        )
        note = ""
        if shared_stock:
            note = (
                f"\nStok bersama nyala ({'jumlah' if stock_mode == 'amount' else 'satuan'}), "
                f"isi awal {stock_quantity:,}. Di produknya, atur `stock_cost` (stok yang kepake per qty)."
                if stock_mode == "amount"
                else f"\nStok bersama nyala (satuan), isi awal {stock_quantity:,}."
            )
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Product Type **{name}** berhasil dibuat dengan ID `{group_id}`.{note}"),
            ephemeral=True,
        )

    @group.command(name="edit", description="Edit Product Type yang udah ada.")
    @app_commands.describe(
        product_group="Product Type yang mau diedit",
        name="Nama baru",
        description="Deskripsi baru",
        emoji="Emoji baru (ketik none buat hapus)",
        shared_stock="Nyalain/matiin pool stok bersama",
        stock_mode="Tipe stok pool baru (unit / amount)",
        stock_label="Satuan baru (ketik none buat hapus)",
    )
    @app_commands.autocomplete(product_group=product_group_autocomplete)
    @staff_only()
    async def edit(
        self,
        interaction: discord.Interaction,
        product_group: int,
        name: app_commands.Range[str, 1, 100] | None = None,
        description: str | None = None,
        emoji: str | None = None,
        shared_stock: bool | None = None,
        stock_mode: StockMode | None = None,
        stock_label: str | None = None,
    ) -> None:
        existing = await product_groups_q.get_product_group(self.bot.db, product_group)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Product Type gak ketemu."), ephemeral=True)
            return
        if emoji and emoji != "none" and not is_valid_emoji(emoji):
            await interaction.response.send_message(embed=embeds.error_embed(_BAD_EMOJI), ephemeral=True)
            return

        updates: dict = {}
        if name is not None:
            updates["name"] = name
        if description is not None:
            updates["description"] = description
        if emoji is not None:
            updates["emoji"] = None if emoji == "none" else emoji
        if shared_stock is not None:
            updates["shared_stock"] = int(shared_stock)
        if stock_mode is not None:
            updates["stock_mode"] = stock_mode
        if stock_label is not None:
            updates["stock_label"] = None if stock_label.lower() == "none" else stock_label[:20]
        if not updates:
            await interaction.response.send_message(
                embed=embeds.error_embed("Gak ada yang diubah -- isi minimal satu parameter."), ephemeral=True
            )
            return
        await product_groups_q.update_product_group(self.bot.db, product_group, **updates)
        warning = ""
        if shared_stock is False and existing["shared_stock"]:
            warning = (
                "\nPool bersama dimatiin -- produk di bawahnya balik pake stok masing-masing "
                "(cek `/product list`, stok mereka mungkin belum diisi)."
            )
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Product Type `#{product_group}` berhasil diupdate.{warning}"), ephemeral=True
        )

    @group.command(name="delete", description="Hapus Product Type (produk di bawahnya TETAP ada).")
    @app_commands.describe(product_group="Product Type yang mau dihapus")
    @app_commands.autocomplete(product_group=product_group_autocomplete)
    @staff_only()
    async def delete(self, interaction: discord.Interaction, product_group: int) -> None:
        existing = await product_groups_q.get_product_group(self.bot.db, product_group)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Product Type gak ketemu."), ephemeral=True)
            return
        await product_groups_q.delete_product_group(self.bot.db, product_group)
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Product Type **{existing['name']}** udah dihapus. Produk di bawahnya GAK ikut kehapus -- "
                "mereka sekarang masuk \"Lainnya\" di Category Type-nya (atau langsung tampil kalau gak ada Product Type lain). "
                "Field checkout tambahan milik Product Type ini ikut kehapus."
            ),
            ephemeral=True,
        )

    @group.command(name="enable", description="Aktifin Product Type biar muncul di /shop.")
    @app_commands.describe(product_group="Product Type yang mau diaktifin")
    @app_commands.autocomplete(product_group=product_group_autocomplete)
    @staff_only()
    async def enable(self, interaction: discord.Interaction, product_group: int) -> None:
        await product_groups_q.set_product_group_enabled(self.bot.db, product_group, True)
        await interaction.response.send_message(embed=embeds.success_embed("Product Type udah diaktifin."), ephemeral=True)

    @group.command(name="disable", description="Nonaktifin Product Type (beserta produknya) dari /shop.")
    @app_commands.describe(product_group="Product Type yang mau dinonaktifin")
    @app_commands.autocomplete(product_group=product_group_autocomplete)
    @staff_only()
    async def disable(self, interaction: discord.Interaction, product_group: int) -> None:
        await product_groups_q.set_product_group_enabled(self.bot.db, product_group, False)
        await interaction.response.send_message(
            embed=embeds.success_embed("Product Type udah dinonaktifin -- produk di bawahnya ikut gak bisa dibeli."),
            ephemeral=True,
        )

    @group.command(name="position", description="Atur posisi urutan Product Type.")
    @app_commands.describe(product_group="Product Type yang mau diatur", position="Posisi baru (makin kecil makin awal)")
    @app_commands.autocomplete(product_group=product_group_autocomplete)
    @staff_only()
    async def position(self, interaction: discord.Interaction, product_group: int, position: int) -> None:
        await product_groups_q.set_product_group_position(self.bot.db, product_group, position)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Product Type `#{product_group}` dipindah ke posisi {position}."), ephemeral=True
        )

    @group.command(name="list", description="Liat Product Type, bisa difilter per Category Type.")
    @app_commands.describe(category_type="Filter berdasarkan Category Type")
    @app_commands.autocomplete(category_type=category_type_autocomplete)
    @staff_only()
    async def list_groups(self, interaction: discord.Interaction, category_type: int | None = None) -> None:
        rows = await product_groups_q.list_product_groups(self.bot.db, category_type_id=category_type)
        await interaction.response.send_message(embed=embeds.product_group_list_embed(rows), ephemeral=True)

    # -- Pool stok bersama --------------------------------------------------

    @group.command(name="stock", description="Atur isi pool stok bersama (isi ulang / kurangi / set).")
    @app_commands.describe(
        product_group="Product Type yang pool-nya mau diatur",
        action="set = timpa angka, add = tambah (restock), subtract = kurangi",
        amount="Jumlahnya (dalam satuan stok, misal 5000 buat 5.000 Robux)",
    )
    @app_commands.autocomplete(product_group=product_group_autocomplete)
    @staff_only()
    async def stock(
        self,
        interaction: discord.Interaction,
        product_group: int,
        action: StockAction,
        amount: app_commands.Range[int, 0, None],
    ) -> None:
        existing = await product_groups_q.get_product_group(self.bot.db, product_group)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Product Type gak ketemu."), ephemeral=True)
            return
        if not existing["shared_stock"]:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Product Type ini gak pake stok bersama. Nyalain dulu lewat "
                    "`/product_group edit shared_stock:True`, atau atur stok tiap produk lewat `/product stock`."
                ),
                ephemeral=True,
            )
            return
        current = int(existing["stock_quantity"])
        new_value = {"set": amount, "add": current + amount, "subtract": max(0, current - amount)}[action]
        await product_groups_q.set_stock(self.bot.db, product_group, new_value)
        unit = f" {existing['stock_label']}" if existing["stock_label"] else ""
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Pool **{existing['name']}**: {current:,}{unit} -> **{new_value:,}{unit}**."
            ),
            ephemeral=True,
        )

    # -- Field checkout tambahan (opsional) -----------------------------------

    @field_group.command(name="add", description="Tambahin field checkout khusus Product Type ini.")
    @app_commands.describe(
        product_group="Product Type yang mau ditambahin field",
        label="Label field yang muncul ke customer",
        field_type="Jenis field-nya",
        required="Apakah customer wajib isi ini",
        placeholder="Teks placeholder di input",
        min_length="Panjang karakter minimal",
        max_length="Panjang karakter maksimal",
        validation="Aturan validasi value",
    )
    @app_commands.autocomplete(product_group=product_group_autocomplete)
    @staff_only()
    async def field_add(
        self,
        interaction: discord.Interaction,
        product_group: int,
        label: app_commands.Range[str, 1, 45],
        field_type: FieldType,
        required: bool = True,
        placeholder: str | None = None,
        min_length: app_commands.Range[int, 0, 4000] = 0,
        max_length: app_commands.Range[int, 1, 4000] = 100,
        validation: Validation = "none",
    ) -> None:
        existing = await product_groups_q.get_product_group(self.bot.db, product_group)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Product Type gak ketemu."), ephemeral=True)
            return
        field_id = await fields_q.create_field(
            self.bot.db, existing["category_type_id"], label, field_type, required, placeholder,
            min_length, max_length, validation, product_group_id=product_group,
        )
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Field **{label}** berhasil ditambahin dengan ID `{field_id}` ke Product Type **{existing['name']}**. "
                "Field ini ditanyain TAMBAHAN setelah field Category Type-nya, cuma buat produk di Product Type ini."
            ),
            ephemeral=True,
        )

    @field_group.command(name="edit", description="Edit field checkout milik Product Type.")
    @app_commands.describe(
        product_group="Product Type tempat field ini berada",
        field_id="ID field yang mau diedit (lihat /product_group field list)",
        label="Label baru",
        required="Status wajib yang baru",
        placeholder="Placeholder baru",
        min_length="Panjang minimal baru",
        max_length="Panjang maksimal baru",
        validation="Aturan validasi baru",
    )
    @app_commands.autocomplete(product_group=product_group_autocomplete)
    @staff_only()
    async def field_edit(
        self,
        interaction: discord.Interaction,
        product_group: int,
        field_id: int,
        label: app_commands.Range[str, 1, 45] | None = None,
        required: bool | None = None,
        placeholder: str | None = None,
        min_length: int | None = None,
        max_length: int | None = None,
        validation: Validation | None = None,
    ) -> None:
        existing = await fields_q.get_field(self.bot.db, field_id)
        if not existing or existing["product_group_id"] != product_group:
            await interaction.response.send_message(
                embed=embeds.error_embed("Field gak ketemu di Product Type ini."), ephemeral=True
            )
            return
        updates = {}
        if label is not None:
            updates["label"] = label
        if required is not None:
            updates["required"] = int(required)
        if placeholder is not None:
            updates["placeholder"] = placeholder
        if min_length is not None:
            updates["min_length"] = min_length
        if max_length is not None:
            updates["max_length"] = max_length
        if validation is not None:
            updates["validation"] = validation
        await fields_q.update_field(self.bot.db, field_id, **updates)
        await interaction.response.send_message(embed=embeds.success_embed("Field berhasil diupdate."), ephemeral=True)

    @field_group.command(name="remove", description="Hapus field checkout milik Product Type.")
    @app_commands.describe(product_group="Product Type tempat field ini berada", field_id="ID field yang mau dihapus")
    @app_commands.autocomplete(product_group=product_group_autocomplete)
    @staff_only()
    async def field_remove(self, interaction: discord.Interaction, product_group: int, field_id: int) -> None:
        existing = await fields_q.get_field(self.bot.db, field_id)
        if not existing or existing["product_group_id"] != product_group:
            await interaction.response.send_message(
                embed=embeds.error_embed("Field gak ketemu di Product Type ini."), ephemeral=True
            )
            return
        await fields_q.delete_field(self.bot.db, field_id)
        await interaction.response.send_message(embed=embeds.success_embed("Field udah dihapus."), ephemeral=True)

    @field_group.command(name="list", description="Liat field checkout tambahan milik Product Type.")
    @app_commands.describe(product_group="Product Type yang mau dicek")
    @app_commands.autocomplete(product_group=product_group_autocomplete)
    @staff_only()
    async def field_list(self, interaction: discord.Interaction, product_group: int) -> None:
        existing = await product_groups_q.get_product_group(self.bot.db, product_group)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Product Type gak ketemu."), ephemeral=True)
            return
        rows = await fields_q.list_group_fields(self.bot.db, product_group)
        type_count = len(await fields_q.list_fields(self.bot.db, existing["category_type_id"]))
        if not rows:
            await interaction.response.send_message(
                embed=embeds.info_embed(
                    f"Checkout Field -- {existing['name']}",
                    "Belum ada field tambahan di Product Type ini. "
                    f"Produknya cuma ditanyain {type_count} field dari Category Type-nya.",
                ),
                ephemeral=True,
            )
            return
        lines = [
            f"`#{r['id']}` **{r['label']}** ({r['field_type']}) "
            f"{'wajib' if r['required'] else 'opsional'} -- "
            f"{r['min_length']}-{r['max_length']} karakter -- validasi: {r['validation']}"
            for r in rows
        ]
        lines.append(
            f"\n-# Ditanyain SETELAH {type_count} field dari Category Type-nya "
            "(`/category_type field list`)."
        )
        await interaction.response.send_message(
            embed=embeds.info_embed(f"Checkout Field -- {existing['name']}", "\n".join(lines)),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProductGroupCog(bot))
