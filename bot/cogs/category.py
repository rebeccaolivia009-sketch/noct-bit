"""Command admin: /category"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import categories as categories_q
from bot.ui import embeds
from bot.utils.autocomplete import category_autocomplete
from bot.utils.permissions import staff_only
from bot.utils.validators import is_valid_emoji


class CategoryCog(commands.Cog):
    """Bikin/edit/hapus kategori, toggle visibility, dan atur urutan."""

    category_group = app_commands.Group(
        name="category", description="Kelola kategori toko.", guild_only=True
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @category_group.command(name="create", description="Bikin kategori baru.")
    @app_commands.describe(
        name="Nama kategori",
        description="Deskripsi opsional",
        emoji="Emoji opsional yang muncul di samping kategori pas /shop, misal \U0001F3AE atau custom emoji server",
    )
    @staff_only()
    async def create(
        self,
        interaction: discord.Interaction,
        name: str,
        description: str | None = None,
        emoji: str | None = None,
    ) -> None:
        if emoji and not is_valid_emoji(emoji):
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Itu kayaknya bukan emoji yang valid. Pake emoji biasa atau custom emoji dari server ini."
                ),
                ephemeral=True,
            )
            return
        category_id = await categories_q.create_category(self.bot.db, name, description, emoji)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Kategori **{name}** berhasil dibuat dengan ID `{category_id}`."),
            ephemeral=True,
        )

    @category_group.command(name="edit", description="Edit kategori yang udah ada.")
    @app_commands.describe(
        category="Kategori yang mau diedit",
        name="Nama baru",
        description="Deskripsi baru",
        emoji="Emoji baru (ketik none buat hapus)",
    )
    @app_commands.autocomplete(category=category_autocomplete)
    @staff_only()
    async def edit(
        self,
        interaction: discord.Interaction,
        category: int,
        name: str | None = None,
        description: str | None = None,
        emoji: str | None = None,
    ) -> None:
        existing = await categories_q.get_category(self.bot.db, category)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Kategori gak ketemu."), ephemeral=True)
            return
        if emoji and emoji != "none" and not is_valid_emoji(emoji):
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Itu kayaknya bukan emoji yang valid. Pake emoji biasa atau custom emoji dari server ini."
                ),
                ephemeral=True,
            )
            return
        await categories_q.update_category(self.bot.db, category, name=name, description=description, emoji=emoji)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Kategori `#{category}` berhasil diupdate."), ephemeral=True
        )

    @category_group.command(name="delete", description="Hapus kategori beserta semua produknya.")
    @app_commands.describe(category="Kategori yang mau dihapus")
    @app_commands.autocomplete(category=category_autocomplete)
    @staff_only()
    async def delete(self, interaction: discord.Interaction, category: int) -> None:
        existing = await categories_q.get_category(self.bot.db, category)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Kategori gak ketemu."), ephemeral=True)
            return
        await categories_q.delete_category(self.bot.db, category)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Kategori **{existing['name']}** beserta produknya udah dihapus."),
            ephemeral=True,
        )

    @category_group.command(name="enable", description="Aktifin kategori biar muncul di /shop.")
    @app_commands.describe(category="Kategori yang mau diaktifin")
    @app_commands.autocomplete(category=category_autocomplete)
    @staff_only()
    async def enable(self, interaction: discord.Interaction, category: int) -> None:
        await categories_q.set_category_enabled(self.bot.db, category, True)
        await interaction.response.send_message(embed=embeds.success_embed("Kategori udah diaktifin."), ephemeral=True)

    @category_group.command(name="disable", description="Nonaktifin kategori, disembunyiin dari /shop.")
    @app_commands.describe(category="Kategori yang mau dinonaktifin")
    @app_commands.autocomplete(category=category_autocomplete)
    @staff_only()
    async def disable(self, interaction: discord.Interaction, category: int) -> None:
        await categories_q.set_category_enabled(self.bot.db, category, False)
        await interaction.response.send_message(embed=embeds.success_embed("Kategori udah dinonaktifin."), ephemeral=True)

    @category_group.command(name="position", description="Atur posisi urutan kategori.")
    @app_commands.describe(category="Kategori yang mau diatur posisinya", position="Posisi baru (makin kecil makin awal)")
    @app_commands.autocomplete(category=category_autocomplete)
    @staff_only()
    async def position(self, interaction: discord.Interaction, category: int, position: int) -> None:
        await categories_q.set_category_position(self.bot.db, category, position)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Kategori `#{category}` dipindah ke posisi {position}."), ephemeral=True
        )

    @category_group.command(name="list", description="Liat semua kategori.")
    @staff_only()
    async def list_categories(self, interaction: discord.Interaction) -> None:
        rows = await categories_q.list_categories(self.bot.db)
        await interaction.response.send_message(embed=embeds.category_list_embed(rows), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CategoryCog(bot))
