"""Command user & admin: /review"""

from __future__ import annotations

from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import orders as orders_q
from bot.database.queries import products as products_q
from bot.database.queries import reviews as reviews_q
from bot.ui import embeds
from bot.utils import activity_log, review_actions
from bot.utils.autocomplete import product_autocomplete
from bot.utils.permissions import staff_only

Rating = Literal[1, 2, 3, 4, 5]


async def _unreviewed_order_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    db = interaction.client.db  # type: ignore[attr-defined]
    rows = await orders_q.list_completed_unreviewed(db, interaction.user.id)
    choices = []
    for r in rows[:25]:
        product = await products_q.get_product(db, r["product_id"])
        name = product["name"] if product else "Produk gak ketemu"
        choices.append(app_commands.Choice(name=f"#{r['id']} -- {name}", value=r["id"]))
    return choices


async def _my_reviewed_order_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    db = interaction.client.db  # type: ignore[attr-defined]
    rows = await reviews_q.list_reviews_for_user(db, interaction.user.id)
    choices = []
    for r in rows[:25]:
        product = await products_q.get_product(db, r["product_id"])
        name = product["name"] if product else "Produk gak ketemu"
        choices.append(app_commands.Choice(name=f"#{r['order_id']} -- {name}", value=r["order_id"]))
    return choices


async def _pending_review_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    db = interaction.client.db  # type: ignore[attr-defined]
    rows = await reviews_q.list_pending_reviews(db, limit=25)
    choices = []
    for r in rows:
        product = await products_q.get_product(db, r["product_id"])
        name = product["name"] if product else "Produk gak ketemu"
        choices.append(app_commands.Choice(name=f"#{r['id']} -- {name} ({r['rating']}/5)", value=r["id"]))
    return choices


class ReviewCog(commands.Cog):
    """Review produk dari customer yang order-nya udah completed & paid -- nunggu approve staff."""

    review_group = app_commands.Group(name="review", description="Kelola review produk.", guild_only=True)
    admin_group = app_commands.Group(
        name="admin", description="Moderasi review.", parent=review_group
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _eligible_order(self, order_id: int, user_id: int):
        order = await orders_q.get_order(self.bot.db, order_id)
        if not order or order["user_id"] != user_id:
            return None
        if order["status"] != "completed" or order["payment_status"] != "paid":
            return None
        return order

    @review_group.command(name="submit", description="Kasih review buat order yang udah selesai.")
    @app_commands.describe(
        order="Order kamu yang udah completed, paid, dan belum direview",
        rating="Rating dari 1 sampe 5",
        review="Teks review kamu",
        anonymous="Sembunyiin nama kamu di review",
    )
    @app_commands.autocomplete(order=_unreviewed_order_autocomplete)
    async def submit(
        self,
        interaction: discord.Interaction,
        order: int,
        rating: Rating,
        review: str | None = None,
        anonymous: bool = False,
    ) -> None:
        order_row = await self._eligible_order(order, interaction.user.id)
        if not order_row:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Order itu belum bisa direview. Harus punya kamu, udah **paid**, "
                    "dan ditandain **completed** sama staff."
                ),
                ephemeral=True,
            )
            return
        existing = await reviews_q.get_review_by_order(self.bot.db, order)
        if existing:
            await interaction.response.send_message(
                embed=embeds.error_embed("Kamu udah pernah review order ini. Pake `/review edit` aja."),
                ephemeral=True,
            )
            return
        await reviews_q.create_review(
            self.bot.db, order, order_row["product_id"], interaction.user.id, rating, review, anonymous
        )
        await interaction.response.send_message(
            embed=embeds.success_embed(
                "Makasih! Review kamu udah dikirim dan lagi nunggu approve staff."
            ),
            ephemeral=True,
        )

    @review_group.command(name="edit", description="Edit review kamu yang udah ada.")
    @app_commands.describe(order="Order yang review-nya mau kamu edit", rating="Rating baru", review="Teks review baru", anonymous="Sembunyiin nama kamu")
    @app_commands.autocomplete(order=_my_reviewed_order_autocomplete)
    async def edit(
        self,
        interaction: discord.Interaction,
        order: int,
        rating: Rating | None = None,
        review: str | None = None,
        anonymous: bool | None = None,
    ) -> None:
        existing = await reviews_q.get_review_by_order(self.bot.db, order)
        if not existing or existing["user_id"] != interaction.user.id:
            await interaction.response.send_message(embed=embeds.error_embed("Review gak ketemu."), ephemeral=True)
            return
        updates: dict = {"status": "pending"}
        if rating is not None:
            updates["rating"] = rating
        if review is not None:
            updates["review_text"] = review
        if anonymous is not None:
            updates["anonymous"] = int(anonymous)
        await reviews_q.update_review(self.bot.db, existing["id"], **updates)
        await interaction.response.send_message(
            embed=embeds.success_embed("Review udah diupdate dan dikirim ulang buat nunggu approve."), ephemeral=True
        )

    @review_group.command(name="delete", description="Hapus review kamu.")
    @app_commands.describe(order="Order yang review-nya mau kamu hapus")
    @app_commands.autocomplete(order=_my_reviewed_order_autocomplete)
    async def delete(self, interaction: discord.Interaction, order: int) -> None:
        existing = await reviews_q.get_review_by_order(self.bot.db, order)
        if not existing or existing["user_id"] != interaction.user.id:
            await interaction.response.send_message(embed=embeds.error_embed("Review gak ketemu."), ephemeral=True)
            return
        await reviews_q.delete_review(self.bot.db, existing["id"])
        await interaction.response.send_message(embed=embeds.success_embed("Review udah dihapus."), ephemeral=True)

    @review_group.command(name="list", description="Liat review yang di-approve dan ringkasan rating buat suatu produk.")
    @app_commands.describe(product="Produk yang mau dilihat review-nya")
    @app_commands.autocomplete(product=product_autocomplete)
    async def list_reviews(self, interaction: discord.Interaction, product: int) -> None:
        product_row = await products_q.get_product(self.bot.db, product)
        if not product_row:
            await interaction.response.send_message(embed=embeds.error_embed("Produk gak ketemu."), ephemeral=True)
            return
        summary = await reviews_q.get_rating_summary(self.bot.db, product)
        recent = await reviews_q.list_reviews_for_product(self.bot.db, product, status="approved", limit=5)

        embed = embeds.rating_distribution_embed(product_row, summary)
        for r in recent:
            author = "Anonim" if r["anonymous"] else f"<@{r['user_id']}>"
            text = r["review_text"] or "*(gak ada review tertulis)*"
            embed.add_field(
                name=f"{r['rating']}/5 -- {author} -- Pembelian Terverifikasi",
                value=text[:200],
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -- Moderasi Admin -----------------------------------------------------

    @admin_group.command(name="approve", description="Approve review yang pending, bikin dia keliatan publik.")
    @app_commands.describe(review_id="Review pending yang mau di-approve")
    @app_commands.autocomplete(review_id=_pending_review_autocomplete)
    @staff_only()
    async def approve(self, interaction: discord.Interaction, review_id: int) -> None:
        if not await reviews_q.get_review(self.bot.db, review_id):
            await interaction.response.send_message(embed=embeds.error_embed("Review gak ketemu."), ephemeral=True)
            return
        await reviews_q.set_review_status(self.bot.db, review_id, "approved")
        posted = await review_actions.post_review_publicly(self.bot, review_id)
        message = "Review udah di-approve."
        if posted:
            message += " Udah diposting ke channel review publik."
        await interaction.response.send_message(embed=embeds.success_embed(message), ephemeral=True)
        await activity_log.log_activity(
            self.bot, interaction.user, "Review Di-approve", f"Review #{review_id} di-approve."
        )

    @admin_group.command(name="reject", description="Reject review yang pending.")
    @app_commands.describe(review_id="Review pending yang mau di-reject")
    @app_commands.autocomplete(review_id=_pending_review_autocomplete)
    @staff_only()
    async def reject(self, interaction: discord.Interaction, review_id: int) -> None:
        await reviews_q.set_review_status(self.bot.db, review_id, "rejected")
        await interaction.response.send_message(embed=embeds.success_embed("Review udah di-reject."), ephemeral=True)
        await activity_log.log_activity(
            self.bot, interaction.user, "Review Di-reject", f"Review #{review_id} di-reject."
        )

    @admin_group.command(name="hide", description="Sembunyiin review yang sebelumnya udah di-approve.")
    @app_commands.describe(review_id="Review yang mau disembunyiin")
    @staff_only()
    async def hide(self, interaction: discord.Interaction, review_id: int) -> None:
        await reviews_q.set_review_status(self.bot.db, review_id, "hidden")
        await interaction.response.send_message(embed=embeds.success_embed("Review udah disembunyiin."), ephemeral=True)
        await activity_log.log_activity(
            self.bot, interaction.user, "Review Disembunyiin", f"Review #{review_id} disembunyiin."
        )

    @admin_group.command(name="delete", description="Hapus review secara permanen.")
    @app_commands.describe(review_id="Review yang mau dihapus")
    @staff_only()
    async def admin_delete(self, interaction: discord.Interaction, review_id: int) -> None:
        await reviews_q.delete_review(self.bot.db, review_id)
        await interaction.response.send_message(embed=embeds.success_embed("Review udah dihapus."), ephemeral=True)
        await activity_log.log_activity(
            self.bot, interaction.user, "Review Dihapus", f"Review #{review_id} dihapus permanen."
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReviewCog(bot))
