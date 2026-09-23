"""Admin commands: /variant"""

from __future__ import annotations

from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import products as products_q
from bot.database.queries import variants as variants_q
from bot.ui import embeds
from bot.utils.autocomplete import product_autocomplete, variant_autocomplete
from bot.utils.permissions import staff_only

DiscountType = Literal["none", "percent", "flat"]


class VariantCog(commands.Cog):
    """Manage multiple priced variants per product."""

    variant_group = app_commands.Group(
        name="variant", description="Manage product variants.", guild_only=True
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @variant_group.command(name="add", description="Add a variant to a product.")
    @app_commands.describe(
        product="Product to add the variant to",
        title="Variant title",
        price="Variant price",
        description="Variant description",
    )
    @app_commands.autocomplete(product=product_autocomplete)
    @staff_only()
    async def add(
        self,
        interaction: discord.Interaction,
        product: int,
        title: str,
        price: app_commands.Range[float, 0, None],
        description: str | None = None,
    ) -> None:
        if not await products_q.get_product(self.bot.db, product):
            await interaction.response.send_message(embed=embeds.error_embed("Product not found."), ephemeral=True)
            return
        variant_id = await variants_q.create_variant(self.bot.db, product, title, description, price)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Variant **{title}** added with ID `{variant_id}`."), ephemeral=True
        )

    @variant_group.command(name="edit", description="Edit a product variant.")
    @app_commands.describe(
        product="Product the variant belongs to",
        variant="Variant to edit",
        title="New title",
        price="New price",
        description="New description",
        discount_type="Discount type, or none to remove it",
        discount_value="Discount value (percent number or flat amount)",
        available="Whether this variant can currently be purchased",
    )
    @app_commands.autocomplete(product=product_autocomplete, variant=variant_autocomplete)
    @staff_only()
    async def edit(
        self,
        interaction: discord.Interaction,
        product: int,
        variant: int,
        title: str | None = None,
        price: float | None = None,
        description: str | None = None,
        discount_type: DiscountType | None = None,
        discount_value: float | None = None,
        available: bool | None = None,
    ) -> None:
        existing = await variants_q.get_variant(self.bot.db, variant)
        if not existing or existing["product_id"] != product:
            await interaction.response.send_message(
                embed=embeds.error_embed("Variant not found on this product."), ephemeral=True
            )
            return
        updates = {}
        if title is not None:
            updates["title"] = title
        if price is not None:
            updates["price"] = price
        if description is not None:
            updates["description"] = description
        if discount_type is not None:
            updates["discount_type"] = None if discount_type == "none" else discount_type
        if discount_value is not None:
            updates["discount_value"] = discount_value
        if available is not None:
            updates["available"] = int(available)
        await variants_q.update_variant(self.bot.db, variant, **updates)
        await interaction.response.send_message(embed=embeds.success_embed("Variant updated."), ephemeral=True)

    @variant_group.command(name="remove", description="Remove a variant from a product.")
    @app_commands.describe(product="Product the variant belongs to", variant="Variant to remove")
    @app_commands.autocomplete(product=product_autocomplete, variant=variant_autocomplete)
    @staff_only()
    async def remove(self, interaction: discord.Interaction, product: int, variant: int) -> None:
        existing = await variants_q.get_variant(self.bot.db, variant)
        if not existing or existing["product_id"] != product:
            await interaction.response.send_message(
                embed=embeds.error_embed("Variant not found on this product."), ephemeral=True
            )
            return
        await variants_q.delete_variant(self.bot.db, variant)
        await interaction.response.send_message(embed=embeds.success_embed("Variant removed."), ephemeral=True)

    @variant_group.command(name="list", description="List a product's variants.")
    @app_commands.describe(product="Product to inspect")
    @app_commands.autocomplete(product=product_autocomplete)
    @staff_only()
    async def list_variants(self, interaction: discord.Interaction, product: int) -> None:
        rows = await variants_q.list_variants(self.bot.db, product)
        if not rows:
            await interaction.response.send_message(
                embed=embeds.info_embed("Variants", "No variants configured for this product."),
                ephemeral=True,
            )
            return
        lines = [
            f"`#{r['id']}` **{r['title']}** -- {r['price']:,.2f} -- "
            f"{'available' if r['available'] else 'unavailable'}"
            for r in rows
        ]
        await interaction.response.send_message(
            embed=embeds.info_embed("Variants", "\n".join(lines)), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VariantCog(bot))
