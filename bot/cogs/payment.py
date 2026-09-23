"""Command admin: /payment"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import payments as payments_q
from bot.ui import embeds
from bot.utils.autocomplete import payment_autocomplete
from bot.utils.permissions import staff_only
from bot.utils.validators import is_valid_emoji


class PaymentCog(commands.Cog):
    """Atur metode pembayaran, instruksi, dan timeout."""

    payment_group = app_commands.Group(
        name="payment", description="Kelola metode pembayaran.", guild_only=True
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @payment_group.command(name="add", description="Tambahin metode pembayaran baru.")
    @app_commands.describe(
        name="Nama metode pembayaran, misal QRIS, Transfer Bank, PayPal, Maybank",
        instructions="Instruksi buat customer (misal nomor rekening, cara bayar)",
        image_url="Gambar QR code / pembayaran buat customer (PNG/JPG/WebP), misal QRIS kamu",
        timeout_minutes="Menit sebelum order yang belum dibayar otomatis expired",
        emoji="Emoji custom/biasa yang muncul di samping metode ini pas dropdown pilih bayar",
    )
    @staff_only()
    async def add(
        self,
        interaction: discord.Interaction,
        name: str,
        instructions: str | None = None,
        image_url: str | None = None,
        timeout_minutes: app_commands.Range[int, 1, 10080] = 30,
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
        payment_id = await payments_q.create_payment_method(
            self.bot.db, name, instructions, timeout_minutes, image_url, emoji
        )
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Metode pembayaran **{name}** berhasil ditambahin dengan ID `{payment_id}`."),
            ephemeral=True,
        )

    @payment_group.command(name="edit", description="Edit metode pembayaran.")
    @app_commands.describe(
        payment="Metode pembayaran yang mau diedit",
        name="Nama baru",
        instructions="Instruksi baru",
        image_url="URL gambar QR code / pembayaran baru (PNG/JPG/WebP)",
        timeout_minutes="Timeout pembayaran baru (menit)",
        emoji="Emoji baru (ketik none buat hapus)",
    )
    @app_commands.autocomplete(payment=payment_autocomplete)
    @staff_only()
    async def edit(
        self,
        interaction: discord.Interaction,
        payment: int,
        name: str | None = None,
        instructions: str | None = None,
        image_url: str | None = None,
        timeout_minutes: int | None = None,
        emoji: str | None = None,
    ) -> None:
        existing = await payments_q.get_payment_method(self.bot.db, payment)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Metode pembayaran gak ketemu."), ephemeral=True)
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
        if instructions is not None:
            updates["instructions"] = instructions
        if image_url is not None:
            updates["image_url"] = image_url
        if timeout_minutes is not None:
            updates["timeout_minutes"] = timeout_minutes
        if emoji is not None:
            updates["emoji"] = None if emoji == "none" else emoji
        await payments_q.update_payment_method(self.bot.db, payment, **updates)
        await interaction.response.send_message(embed=embeds.success_embed("Metode pembayaran berhasil diupdate."), ephemeral=True)

    @payment_group.command(name="delete", description="Hapus metode pembayaran.")
    @app_commands.describe(payment="Metode pembayaran yang mau dihapus")
    @app_commands.autocomplete(payment=payment_autocomplete)
    @staff_only()
    async def delete(self, interaction: discord.Interaction, payment: int) -> None:
        existing = await payments_q.get_payment_method(self.bot.db, payment)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Metode pembayaran gak ketemu."), ephemeral=True)
            return
        await payments_q.delete_payment_method(self.bot.db, payment)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Metode pembayaran **{existing['name']}** udah dihapus."), ephemeral=True
        )

    @payment_group.command(name="enable", description="Aktifin metode pembayaran.")
    @app_commands.describe(payment="Metode pembayaran yang mau diaktifin")
    @app_commands.autocomplete(payment=payment_autocomplete)
    @staff_only()
    async def enable(self, interaction: discord.Interaction, payment: int) -> None:
        await payments_q.set_payment_enabled(self.bot.db, payment, True)
        await interaction.response.send_message(embed=embeds.success_embed("Metode pembayaran udah diaktifin."), ephemeral=True)

    @payment_group.command(name="disable", description="Nonaktifin metode pembayaran.")
    @app_commands.describe(payment="Metode pembayaran yang mau dinonaktifin")
    @app_commands.autocomplete(payment=payment_autocomplete)
    @staff_only()
    async def disable(self, interaction: discord.Interaction, payment: int) -> None:
        await payments_q.set_payment_enabled(self.bot.db, payment, False)
        await interaction.response.send_message(embed=embeds.success_embed("Metode pembayaran udah dinonaktifin."), ephemeral=True)

    @payment_group.command(name="list", description="Liat semua metode pembayaran.")
    @staff_only()
    async def list_payments(self, interaction: discord.Interaction) -> None:
        rows = await payments_q.list_payment_methods(self.bot.db)
        await interaction.response.send_message(embed=embeds.payment_list_embed(rows), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PaymentCog(bot))
