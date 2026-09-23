"""
Command admin: /category_type

Ada di antara Category dan Product (Category -> Category Type -> Product).
Gantiin command /variant yang lama -- daripada satu produk punya beberapa
sub-opsi berharga, sekarang produk dikelompokin di bawah satu tipe dan tiap
produk jadi barang sendiri yang independen dan berharga penuh. Dynamic
checkout field (subgroup nested /category_type field) juga ada di sini,
jadi semua produk di bawah satu tipe otomatis share checkout field yang
sama.
"""

from __future__ import annotations

from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import categories as categories_q
from bot.database.queries import category_types as category_types_q
from bot.database.queries import fields as fields_q
from bot.ui import embeds
from bot.utils.autocomplete import category_autocomplete, category_type_autocomplete
from bot.utils.permissions import staff_only
from bot.utils.validators import is_valid_emoji

FieldType = Literal[
    "username", "userid", "login", "email", "password", "serverid", "gameid", "custom"
]
Validation = Literal["none", "numeric", "alpha", "alphanumeric", "email"]


class CategoryTypeCog(commands.Cog):
    """Kelola Category Type, plus konfigurasi dynamic checkout field."""

    category_type_group = app_commands.Group(
        name="category_type", description="Kelola category type (di antara Category dan Product).", guild_only=True
    )
    field_group = app_commands.Group(
        name="field",
        description="Kelola dynamic checkout input field milik category type.",
        parent=category_type_group,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- CRUD Category Type ---------------------------------------------------

    @category_type_group.command(name="create", description="Bikin category type baru di bawah kategori.")
    @app_commands.describe(
        category="Kategori induk buat tipe ini",
        name="Nama category type",
        description="Deskripsi opsional",
        emoji="Emoji opsional yang muncul di samping tipe ini pas /shop",
    )
    @app_commands.autocomplete(category=category_autocomplete)
    @staff_only()
    async def create(
        self,
        interaction: discord.Interaction,
        category: int,
        name: str,
        description: str | None = None,
        emoji: str | None = None,
    ) -> None:
        if not await categories_q.get_category(self.bot.db, category):
            await interaction.response.send_message(embed=embeds.error_embed("Kategori gak ketemu."), ephemeral=True)
            return
        if emoji and not is_valid_emoji(emoji):
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Itu kayaknya bukan emoji yang valid. Pake emoji biasa atau custom emoji dari server ini."
                ),
                ephemeral=True,
            )
            return
        category_type_id = await category_types_q.create_category_type(
            self.bot.db, category, name, description, emoji
        )
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Category type **{name}** berhasil dibuat dengan ID `{category_type_id}`."),
            ephemeral=True,
        )

    @category_type_group.command(name="edit", description="Edit category type yang udah ada.")
    @app_commands.describe(
        category_type="Category type yang mau diedit",
        name="Nama baru",
        description="Deskripsi baru",
        emoji="Emoji baru (ketik none buat hapus)",
        category="Pindah ke kategori induk yang lain",
    )
    @app_commands.autocomplete(category_type=category_type_autocomplete, category=category_autocomplete)
    @staff_only()
    async def edit(
        self,
        interaction: discord.Interaction,
        category_type: int,
        name: str | None = None,
        description: str | None = None,
        emoji: str | None = None,
        category: int | None = None,
    ) -> None:
        existing = await category_types_q.get_category_type(self.bot.db, category_type)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Category type gak ketemu."), ephemeral=True)
            return
        if emoji and emoji != "none" and not is_valid_emoji(emoji):
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Itu kayaknya bukan emoji yang valid. Pake emoji biasa atau custom emoji dari server ini."
                ),
                ephemeral=True,
            )
            return
        await category_types_q.update_category_type(
            self.bot.db, category_type, name=name, description=description, emoji=emoji, category_id=category
        )
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Category type `#{category_type}` berhasil diupdate."), ephemeral=True
        )

    @category_type_group.command(name="delete", description="Hapus category type beserta semua produknya.")
    @app_commands.describe(category_type="Category type yang mau dihapus")
    @app_commands.autocomplete(category_type=category_type_autocomplete)
    @staff_only()
    async def delete(self, interaction: discord.Interaction, category_type: int) -> None:
        existing = await category_types_q.get_category_type(self.bot.db, category_type)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Category type gak ketemu."), ephemeral=True)
            return
        await category_types_q.delete_category_type(self.bot.db, category_type)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Category type **{existing['name']}** beserta produknya udah dihapus."),
            ephemeral=True,
        )

    @category_type_group.command(name="enable", description="Aktifin category type biar muncul di /shop.")
    @app_commands.describe(category_type="Category type yang mau diaktifin")
    @app_commands.autocomplete(category_type=category_type_autocomplete)
    @staff_only()
    async def enable(self, interaction: discord.Interaction, category_type: int) -> None:
        await category_types_q.set_category_type_enabled(self.bot.db, category_type, True)
        await interaction.response.send_message(embed=embeds.success_embed("Category type udah diaktifin."), ephemeral=True)

    @category_type_group.command(name="disable", description="Nonaktifin category type, disembunyiin dari /shop.")
    @app_commands.describe(category_type="Category type yang mau dinonaktifin")
    @app_commands.autocomplete(category_type=category_type_autocomplete)
    @staff_only()
    async def disable(self, interaction: discord.Interaction, category_type: int) -> None:
        await category_types_q.set_category_type_enabled(self.bot.db, category_type, False)
        await interaction.response.send_message(embed=embeds.success_embed("Category type udah dinonaktifin."), ephemeral=True)

    @category_type_group.command(name="position", description="Atur posisi urutan category type.")
    @app_commands.describe(category_type="Category type yang mau diatur posisinya", position="Posisi baru (makin kecil makin awal)")
    @app_commands.autocomplete(category_type=category_type_autocomplete)
    @staff_only()
    async def position(self, interaction: discord.Interaction, category_type: int, position: int) -> None:
        await category_types_q.set_category_type_position(self.bot.db, category_type, position)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Category type `#{category_type}` dipindah ke posisi {position}."),
            ephemeral=True,
        )

    @category_type_group.command(name="list", description="Liat category type, bisa difilter per kategori.")
    @app_commands.describe(category="Filter berdasarkan kategori induk")
    @app_commands.autocomplete(category=category_autocomplete)
    @staff_only()
    async def list_category_types(self, interaction: discord.Interaction, category: int | None = None) -> None:
        rows = await category_types_q.list_category_types(self.bot.db, category_id=category)
        await interaction.response.send_message(embed=embeds.category_type_list_embed(rows), ephemeral=True)

    # -- Dynamic checkout field (dishare semua produk di bawah tipe ini) ----

    @field_group.command(name="add", description="Tambahin dynamic checkout input field ke category type.")
    @app_commands.describe(
        category_type="Category type yang mau ditambahin field",
        label="Label field yang muncul ke customer",
        field_type="Jenis field-nya",
        required="Apakah customer wajib isi ini",
        placeholder="Teks placeholder di input",
        min_length="Panjang karakter minimal",
        max_length="Panjang karakter maksimal",
        validation="Aturan validasi value",
    )
    @app_commands.autocomplete(category_type=category_type_autocomplete)
    @staff_only()
    async def field_add(
        self,
        interaction: discord.Interaction,
        category_type: int,
        label: str,
        field_type: FieldType,
        required: bool = True,
        placeholder: str | None = None,
        min_length: app_commands.Range[int, 0, 4000] = 0,
        max_length: app_commands.Range[int, 1, 4000] = 100,
        validation: Validation = "none",
    ) -> None:
        if not await category_types_q.get_category_type(self.bot.db, category_type):
            await interaction.response.send_message(embed=embeds.error_embed("Category type gak ketemu."), ephemeral=True)
            return
        field_id = await fields_q.create_field(
            self.bot.db, category_type, label, field_type, required, placeholder,
            min_length, max_length, validation,
        )
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Field **{label}** berhasil ditambahin dengan ID `{field_id}` -- semua produk di bawah "
                "category type ini bakal pake field ini juga."
            ),
            ephemeral=True,
        )

    @field_group.command(name="edit", description="Edit dynamic checkout input field.")
    @app_commands.describe(
        category_type="Category type tempat field ini berada",
        field_id="ID field yang mau diedit (lihat /category_type field list)",
        label="Label baru",
        required="Status wajib yang baru",
        placeholder="Placeholder baru",
        min_length="Panjang minimal baru",
        max_length="Panjang maksimal baru",
        validation="Aturan validasi baru",
    )
    @app_commands.autocomplete(category_type=category_type_autocomplete)
    @staff_only()
    async def field_edit(
        self,
        interaction: discord.Interaction,
        category_type: int,
        field_id: int,
        label: str | None = None,
        required: bool | None = None,
        placeholder: str | None = None,
        min_length: int | None = None,
        max_length: int | None = None,
        validation: Validation | None = None,
    ) -> None:
        existing = await fields_q.get_field(self.bot.db, field_id)
        if not existing or existing["category_type_id"] != category_type:
            await interaction.response.send_message(
                embed=embeds.error_embed("Field gak ketemu di category type ini."), ephemeral=True
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

    @field_group.command(name="remove", description="Hapus dynamic checkout input field.")
    @app_commands.describe(category_type="Category type tempat field ini berada", field_id="ID field yang mau dihapus")
    @app_commands.autocomplete(category_type=category_type_autocomplete)
    @staff_only()
    async def field_remove(self, interaction: discord.Interaction, category_type: int, field_id: int) -> None:
        existing = await fields_q.get_field(self.bot.db, field_id)
        if not existing or existing["category_type_id"] != category_type:
            await interaction.response.send_message(
                embed=embeds.error_embed("Field gak ketemu di category type ini."), ephemeral=True
            )
            return
        await fields_q.delete_field(self.bot.db, field_id)
        await interaction.response.send_message(embed=embeds.success_embed("Field udah dihapus."), ephemeral=True)

    @field_group.command(name="list", description="Liat dynamic checkout input field milik category type.")
    @app_commands.describe(category_type="Category type yang mau dicek")
    @app_commands.autocomplete(category_type=category_type_autocomplete)
    @staff_only()
    async def field_list(self, interaction: discord.Interaction, category_type: int) -> None:
        rows = await fields_q.list_fields(self.bot.db, category_type)
        if not rows:
            await interaction.response.send_message(
                embed=embeds.info_embed("Checkout Field", "Belum ada field yang diatur buat category type ini."),
                ephemeral=True,
            )
            return
        lines = [
            f"`#{r['id']}` **{r['label']}** ({r['field_type']}) "
            f"{'wajib' if r['required'] else 'opsional'} -- "
            f"{r['min_length']}-{r['max_length']} karakter -- validasi: {r['validation']}"
            for r in rows
        ]
        await interaction.response.send_message(
            embed=embeds.info_embed("Checkout Field", "\n".join(lines)), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CategoryTypeCog(bot))
