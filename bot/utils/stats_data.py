"""
Ngumpulin data kartu Statistik Pembelian (/stats) buat satu pembeli.

Aturan hitungnya SAMA dengan leaderboard (lewat get_spender_profile):
cuma order completed + paid, akun yang dikecualiin dari leaderboard
(/settings leaderboard_exclude) gak punya peringkat.
"""

from __future__ import annotations

import discord

from bot.database.queries import buyer_stats as buyer_stats_q
from bot.database.queries import customer_stats as customer_stats_q
from bot.database.queries import leaderboard as leaderboard_q
from bot.database.queries import orders as orders_q
from bot.utils import stats_style as style
from bot.utils import tiers as tiers_util
from bot.utils.helpers import RuntimeSettings

HISTORY_PAGE_SIZE = 8


def _avatar_url(member: discord.abc.User) -> str | None:
    try:
        return member.display_avatar.replace(size=256).url
    except Exception:  # noqa: BLE001
        return None


async def load_card_data(bot, guild: discord.Guild | None, member: discord.abc.User) -> style.CardData:
    """Semua yang dibutuhin kartu: statistik, peringkat, tier, level gaya,
    dan info eksklusif (yang relevan buat levelnya aja yang diambil)."""
    db = bot.db
    data = style.CardData(user_id=member.id, name=member.display_name, avatar_url=_avatar_url(member))
    data.currency = await buyer_stats_q.get_store_currency(db)

    excluded = await RuntimeSettings(db).leaderboard_excluded_user_ids()
    if member.id in excluded:
        data.excluded = True
        return data

    profile = await leaderboard_q.get_spender_profile(db, member.id, excluded)
    if profile is None:
        return data  # belum ada pesanan completed+paid -> kartu L0

    data.spend = profile["total_spent"]
    data.orders = profile["total_orders"]
    data.rank = profile["rank"]
    data.total_spenders = profile["total_spenders"]
    data.gap_to_next = profile["gap_to_next"]

    tiers = await tiers_util.load_tiers(db, guild.id) if guild is not None else []
    reached = tiers_util.pick_tier(tiers, data.spend)
    upcoming = tiers_util.next_tier_after(tiers, data.spend)
    if reached is not None:
        data.tier_name = reached.name
    if upcoming is not None:
        floor = reached.min_spend if reached is not None else 0.0
        span = upcoming.min_spend - floor
        data.next_tier_name = upcoming.name
        data.next_remaining = upcoming.min_spend - data.spend
        data.progress = max(0.0, min(1.0, (data.spend - floor) / span)) if span > 0 else 0.0

    data.level, data.crown = style.pick_level(data.spend, data.rank, data.total_spenders, tiers)
    chosen = style.style_for(data.level, data.crown)

    # Info eksklusif cuma diambil kalau gayanya emang nampilin (hemat query).
    if any(p in chosen.perks for p in ("since", "favorite", "average", "biggest", "last")):
        extras = await customer_stats_q.get_extras(db, member.id)
        data.first_order_at = style.parse_db_time(extras["first_at"])
        data.last_order_at = style.parse_db_time(extras["last_at"])
        data.biggest = extras["biggest"]
        data.favorite_name = extras["favorite_name"]
        data.favorite_qty = extras["favorite_qty"]
    if "badge" in chosen.perks:   # badge custom cuma buat top 1-3 (aturan leaderboard)
        badge = await leaderboard_q.get_badge(db, member.id)
        data.badge_text = badge["text"] if badge else None
    if "lead" in chosen.perks:
        top = await leaderboard_q.get_top_spenders(db, 2, excluded)
        if len(top) >= 2:
            data.lead_over_second = data.spend - float(top[1]["total_spent"])
    return data


async def load_history_page(db, user_id: int, page: int) -> tuple[list[tuple], int, int]:
    """(daftar (order, items) halaman ini, total pesanan, total halaman)."""
    total = await customer_stats_q.count_orders(db, user_id)
    pages = max(1, -(-total // HISTORY_PAGE_SIZE))
    page = max(0, min(page, pages - 1))
    rows = await customer_stats_q.list_orders(db, user_id, HISTORY_PAGE_SIZE, page * HISTORY_PAGE_SIZE)
    entries = [(row, await orders_q.list_order_items(db, row["id"])) for row in rows]
    return entries, total, pages
