"""Shared autocomplete callbacks for category/category_type/product/payment/order options."""

from __future__ import annotations

import discord
from discord import app_commands

from bot.database.queries import categories as categories_q
from bot.database.queries import category_types as category_types_q
from bot.database.queries import orders as orders_q
from bot.database.queries import payments as payments_q
from bot.database.queries import product_groups as product_groups_q
from bot.database.queries import products as products_q


async def category_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    db = interaction.client.db  # type: ignore[attr-defined]
    rows = await categories_q.list_categories(db)
    current_lower = current.lower()
    matches = [r for r in rows if current_lower in r["name"].lower()]
    return [
        app_commands.Choice(name=f"#{r['id']} -- {r['name']}", value=r["id"])
        for r in matches[:25]
    ]


async def category_type_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    db = interaction.client.db  # type: ignore[attr-defined]
    rows = await category_types_q.search_category_types(db, current, limit=25)
    return [
        app_commands.Choice(name=f"#{r['id']} -- {r['name']}", value=r["id"]) for r in rows
    ]


NO_GROUP_VALUE = 0  # nilai spesial "lepas dari Product Type" di /product create|edit


async def product_group_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    """Autocomplete Product Type. Kalau command-nya lagi punya
    `category_type` yang udah diisi (atau `product` yang lagi diedit),
    hasilnya cuma Product Type di Category Type itu -- biar gak salah
    pilih Product Type dari tipe lain. Kalau Product Type-nya boleh
    dikosongin, opsi "(Tanpa Product Type)" ikut muncul di paling atas."""
    db = interaction.client.db  # type: ignore[attr-defined]
    namespace = interaction.namespace

    category_type_id = None
    raw_type = getattr(namespace, "category_type", None)
    try:
        category_type_id = int(raw_type) if raw_type not in (None, "") else None
    except (TypeError, ValueError):
        category_type_id = None
    if category_type_id is None:
        raw_product = getattr(namespace, "product", None)
        try:
            product = await products_q.get_product(db, int(raw_product)) if raw_product not in (None, "") else None
        except (TypeError, ValueError):
            product = None
        if product is not None:
            category_type_id = product["category_type_id"]

    rows = await product_groups_q.search_product_groups(db, current, category_type_id=category_type_id, limit=24)
    choices = [
        app_commands.Choice(name=f"#{r['id']} -- {r['name']}", value=r["id"]) for r in rows
    ]
    wants_none = not current.strip() or any(w in current.lower() for w in ("tanpa", "none", "lepas"))
    # Opsi "lepas dari Product Type" cuma masuk akal di /product create|edit
    # (bukan di /product_group edit, yang namanya sama-sama "edit").
    parent = getattr(interaction.command, "parent", None)
    if wants_none and parent is not None and parent.name == "product":
        choices.insert(0, app_commands.Choice(name="(Tanpa Product Type)", value=NO_GROUP_VALUE))
    return choices[:25]


async def product_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    db = interaction.client.db  # type: ignore[attr-defined]
    rows = await products_q.search_products(db, current, limit=25)
    return [
        app_commands.Choice(name=f"#{r['id']} -- {r['name']}", value=r["id"]) for r in rows
    ]


async def payment_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    db = interaction.client.db  # type: ignore[attr-defined]
    rows = await payments_q.list_payment_methods(db)
    current_lower = current.lower()
    matches = [r for r in rows if current_lower in r["name"].lower()]
    return [
        app_commands.Choice(name=f"#{r['id']} -- {r['name']}", value=r["id"])
        for r in matches[:25]
    ]


async def my_order_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    db = interaction.client.db  # type: ignore[attr-defined]
    rows = await orders_q.list_orders_for_user(db, interaction.user.id, limit=25)
    return [
        app_commands.Choice(
            name=f"#{r['id']} -- {r['status'].title()} -- {r['total_price']:,.2f} {r['currency_label']}",
            value=r["id"],
        )
        for r in rows
    ]


async def any_order_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[int]]:
    db = interaction.client.db  # type: ignore[attr-defined]
    rows = await orders_q.list_orders(db, limit=25)
    return [
        app_commands.Choice(
            name=f"#{r['id']} -- {r['status'].title()} -- {r['total_price']:,.2f} {r['currency_label']}",
            value=r["id"],
        )
        for r in rows
    ]
