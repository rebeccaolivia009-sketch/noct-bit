"""Command admin: /order. Command user: /orders."""

from __future__ import annotations

from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import orders as orders_q
from bot.database.queries import payments as payments_q
from bot.database.queries import products as products_q
from bot.ui import embeds
from bot.utils import activity_log, order_actions
from bot.utils.autocomplete import any_order_autocomplete
from bot.utils.permissions import staff_only

OrderStatus = Literal["pending", "processing", "completed", "cancelled", "refunded"]
PaymentStatus = Literal["pending", "paid", "expired", "cancelled"]


class OrderCog(commands.Cog):
    """Kelola order buat admin (/order) dan riwayat order customer (/orders)."""

    order_group = app_commands.Group(name="order", description="Kelola order customer.", guild_only=True)

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _full_embed(self, order_id: int) -> discord.Embed:
        db = self.bot.db
        order = await orders_q.get_order(db, order_id)
        product = await products_q.get_product(db, order["product_id"])
        payment = await payments_q.get_payment_method(db, order["payment_method_id"]) if order["payment_method_id"] else None
        field_values = await orders_q.get_field_values(db, order_id)
        return embeds.order_summary_embed(order, product, payment, field_values)

    @order_group.command(name="view", description="Liat detail lengkap suatu order.")
    @app_commands.describe(order="Order yang mau dilihat")
    @app_commands.autocomplete(order=any_order_autocomplete)
    @staff_only()
    async def view(self, interaction: discord.Interaction, order: int) -> None:
        existing = await orders_q.get_order(self.bot.db, order)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Order gak ketemu."), ephemeral=True)
            return
        await interaction.response.send_message(embed=await self._full_embed(order), ephemeral=True)

    @order_group.command(name="list", description="Liat order terbaru, bisa difilter per status.")
    @app_commands.describe(status="Filter berdasarkan status order")
    @staff_only()
    async def list_orders(self, interaction: discord.Interaction, status: OrderStatus | None = None) -> None:
        rows = await orders_q.list_orders(self.bot.db, status=status, limit=25)
        if not rows:
            await interaction.response.send_message(
                embed=embeds.info_embed("Order", "Belum ada order nih."), ephemeral=True
            )
            return
        lines = [
            f"`#{r['id']}` -- {r['status'].title()} / {r['payment_status'].title()} -- "
            f"{r['total_price']:,.2f} {r['currency_label']} -- <@{r['user_id']}>"
            for r in rows
        ]
        await interaction.response.send_message(
            embed=embeds.info_embed("Order", "\n".join(lines)), ephemeral=True
        )

    @order_group.command(name="status", description="Atur status order secara manual.")
    @app_commands.describe(
        order="Order yang mau diupdate",
        status="Status baru",
        reason="Alasan yang ditunjukin ke customer (dipake buat cancelled/refunded)",
    )
    @app_commands.autocomplete(order=any_order_autocomplete)
    @staff_only()
    async def set_status(
        self, interaction: discord.Interaction, order: int, status: OrderStatus, reason: str | None = None
    ) -> None:
        existing = await orders_q.get_order(self.bot.db, order)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Order gak ketemu."), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        if status == "completed":
            ok, message = await order_actions.mark_completed(self.bot, order, interaction.user)
        elif status == "cancelled":
            ok, message = await order_actions.cancel_order(self.bot, order, reason, interaction.user)
        elif status == "refunded":
            ok, message = await order_actions.refund_order(self.bot, order, reason, interaction.user)
        else:
            await orders_q.set_order_status(self.bot.db, order, status)
            ok, message = True, f"Status order `#{order}` diatur jadi **{status}**."
            await activity_log.log_activity(
                self.bot, interaction.user, "Status Order Diubah Manual",
                f"Order #{order} diatur manual jadi **{status}**.",
            )
        await interaction.followup.send(
            embed=embeds.success_embed(message) if ok else embeds.error_embed(message), ephemeral=True
        )

    @order_group.command(name="payment_status", description="Atur status pembayaran order secara manual.")
    @app_commands.describe(order="Order yang mau diupdate", payment_status="Status pembayaran baru")
    @app_commands.autocomplete(order=any_order_autocomplete)
    @staff_only()
    async def set_payment_status(
        self, interaction: discord.Interaction, order: int, payment_status: PaymentStatus
    ) -> None:
        existing = await orders_q.get_order(self.bot.db, order)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Order gak ketemu."), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        if payment_status == "paid":
            ok, message = await order_actions.mark_paid(self.bot, order, interaction.user)
        else:
            await orders_q.set_payment_status(self.bot.db, order, payment_status)
            ok, message = True, f"Status pembayaran order `#{order}` diatur jadi **{payment_status}**."
            await activity_log.log_activity(
                self.bot, interaction.user, "Status Pembayaran Diubah Manual",
                f"Status pembayaran order #{order} diatur manual jadi **{payment_status}**.",
            )
        await interaction.followup.send(
            embed=embeds.success_embed(message) if ok else embeds.error_embed(message), ephemeral=True
        )

    @order_group.command(name="message", description="Kirim pesan ke customer soal order-nya (dikirim lewat DM).")
    @app_commands.describe(order="Order yang mau dikirimin pesan soal ini", message="Pesan yang mau dikirim")
    @app_commands.autocomplete(order=any_order_autocomplete)
    @staff_only()
    async def message_customer(self, interaction: discord.Interaction, order: int, message: str) -> None:
        existing = await orders_q.get_order(self.bot.db, order)
        if not existing:
            await interaction.response.send_message(embed=embeds.error_embed("Order gak ketemu."), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        embed = embeds.info_embed(f"Pesan soal Order #{order}", message)
        sent = await order_actions.send_message_to_customer(self.bot, existing["user_id"], embed, order)
        await interaction.followup.send(
            embed=embeds.success_embed("Pesan udah dikirim.")
            if sent
            else embeds.error_embed("Gak bisa DM customer -- mungkin DM-nya lagi ditutup."),
            ephemeral=True,
        )

    @app_commands.command(name="orders", description="Liat riwayat order kamu.")
    @app_commands.guild_only()
    async def orders(self, interaction: discord.Interaction) -> None:
        rows = await orders_q.list_orders_for_user(self.bot.db, interaction.user.id, limit=25)
        if not rows:
            await interaction.response.send_message(
                embed=embeds.info_embed("Order Kamu", "Kamu belum pernah pesen apa-apa nih."), ephemeral=True
            )
            return
        lines = [
            f"`#{r['id']}` -- {r['status'].title()} / {r['payment_status'].title()} -- "
            f"{r['total_price']:,.2f} {r['currency_label']}"
            for r in rows
        ]
        await interaction.response.send_message(
            embed=embeds.info_embed("Order Kamu", "\n".join(lines)), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OrderCog(bot))
