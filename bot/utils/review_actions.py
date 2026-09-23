"""
Posting review yang udah di-approve ke channel review publik
(`/settings reviews_channel`) buat reputasi toko / social proof -- ini
showcase publik yang bisa dilihat siapa aja di server, bukan antrian
moderasi staff. Staff tetep yang nentuin apa yang boleh tampil lewat
command `/review admin approve|reject|hide|delete` yang udah ada; module ini
cuma ngurusin review yang udah di-approve biar nongol di channel showcase.
"""

from __future__ import annotations

import discord

from bot.core.logger import logger
from bot.database.queries import products as products_q
from bot.database.queries import reviews as reviews_q
from bot.ui import components
from bot.utils.helpers import RuntimeSettings


async def post_review_publicly(bot, review_id: int) -> bool:
    """Return True kalau review-nya berhasil diposting, False kalau channel
    review belum diatur (atau postingnya gagal karena hal lain) -- caller
    make ini buat nentuin apakah perlu disebutin di pesan konfirmasi
    mereka."""
    db = bot.db
    runtime = RuntimeSettings(db)
    channel_id = await runtime.reviews_channel_id()
    if not channel_id:
        return False

    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return False

    review = await reviews_q.get_review(db, review_id)
    if not review or review["status"] != "approved":
        return False

    product = await products_q.get_product(db, review["product_id"])
    if not product:
        return False

    author_display = "Anonim"
    author_avatar_url = None
    if not review["anonymous"]:
        try:
            user = bot.get_user(review["user_id"]) or await bot.fetch_user(review["user_id"])
            # embed.set_author(name=...) cuma render teks polos -- beda
            # sama pesan atau field/description embed biasa, dia GAK
            # nge-resolve syntax @mention jadi nama yang bisa diklik, jadi
            # string "<@123...>" literal bakal muncul apa adanya. Nama
            # display asli harus di-fetch dan dipake langsung.
            author_display = user.display_name
            author_avatar_url = user.display_avatar.url
        except discord.HTTPException:
            author_display = f"User {review['user_id']}"
            author_avatar_url = None

    # Fallback banner diambil dari /settings review_banner_image, bukan
    # avatar bot -- kalau staff belum atur, kartu review tanpa foto ya
    # tampil tanpa banner sama sekali.
    fallback_banner_url = await runtime.review_banner_url()
    container = components.review_card_container(
        review,
        product,
        author_display,
        emoji_title=await runtime.review_card_emoji_title(),
        emoji_user=await runtime.review_card_emoji_user(),
        emoji_product=await runtime.review_card_emoji_product(),
        emoji_rating=await runtime.review_card_emoji_rating(),
        emoji_star_filled=await runtime.review_card_emoji_star_filled(),
        emoji_star_empty=await runtime.review_card_emoji_star_empty(),
        emoji_message=await runtime.review_card_emoji_message(),
        author_avatar_url=author_avatar_url,
        banner_url=fallback_banner_url,
        verified=True,
    )

    try:
        await channel.send(view=components.NoctraLayout(container, timeout=None))
        return True
    except discord.HTTPException:
        logger.exception("Gagal posting review #%s ke channel review.", review_id)
        return False
