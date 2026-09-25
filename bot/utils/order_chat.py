"""Panel chat per-order di channel order-log -- SATU pesan per order yang
di-EDIT in-place (bukan pesan baru) tiap ada balesan customer ATAU staff,
biar channel order-log gak kebanjiran notif baru buat obrolan yang sama.
Dipake bareng dari bot.utils.order_actions.forward_to_staff (pesan
customer) dan send_message_to_customer (balesan staff)."""

from __future__ import annotations

from datetime import datetime, timezone

import discord

from bot.core.logger import logger
from bot.database.queries import order_chat as order_chat_q
from bot.ui import embeds
from bot.utils.helpers import RuntimeSettings

# Di bawah limit embed description Discord (4096) -- sisanya buat header
# "Chat -- Order #X" dan field Customer yang nempel di embed yang sama.
MAX_TRANSCRIPT_CHARS = 3500


def now_str() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M")


def _build_embed(order_id: int, customer: discord.abc.User | None, transcript: str, image_url: str | None) -> discord.Embed:
    embed = embeds.info_embed(
        f"Chat -- Order #{order_id}", transcript or "*(belum ada obrolan)*", image_url=image_url
    )
    if customer is not None:
        embed.add_field(name="Customer", value=f"{customer.mention} ({customer})", inline=False)
    return embed


async def append_and_refresh(
    bot, order_id: int, customer: discord.abc.User, line: str, image_url: str | None = None,
) -> bool:
    """Nambahin satu baris ke transkrip panel chat order ini, terus
    edit-in-place pesannya di channel order-log (bikin baru kalau belum
    pernah ada, atau pesan lamanya udah kehapus/gak keakses).
    `image_url` (opsional) nge-update gambar yang keliatan di panel ke
    gambar TERBARU -- dari customer ATAU staff, beda sama
    orders.payment_proof_url yang sengaja cuma nyimpen gambar PERTAMA
    doang (lihat order_actions.forward_to_staff)."""
    db = bot.db
    state = await order_chat_q.get_panel(db, order_id)

    prev_transcript = state["transcript"] if state else ""
    transcript = f"{prev_transcript}\n{line}".strip() if prev_transcript else line
    if len(transcript) > MAX_TRANSCRIPT_CHARS:
        # Potong dari DEPAN (histori paling lama yang dibuang), terus
        # geser ke baris terdekat biar gak motong tengah kalimat.
        transcript = transcript[-MAX_TRANSCRIPT_CHARS:]
        newline_idx = transcript.find("\n")
        if newline_idx != -1:
            transcript = transcript[newline_idx + 1:]

    latest_image = image_url or (state["latest_image_url"] if state else None)
    embed = _build_embed(order_id, customer, transcript, latest_image)

    # Import ditunda: bot.ui.views ngimport bot.utils.order_actions (yang
    # manggil module ini) di level atas buat ReplyButton, jadi kalau
    # di-import balik di sini di level module bakal circular.
    from bot.ui.views import ReplyButton

    view = discord.ui.View(timeout=None)
    view.add_item(ReplyButton(order_id))

    channel = bot.get_channel(state["channel_id"]) if state else None
    message = None
    if state and isinstance(channel, discord.TextChannel):
        try:
            message = await channel.fetch_message(state["message_id"])
        except discord.HTTPException:
            message = None  # kehapus/gak keakses -- fallback ke posting baru di bawah

    if message is not None:
        try:
            await message.edit(embed=embed, view=view)
            await order_chat_q.save_panel(db, order_id, channel.id, message.id, transcript, latest_image)
            return True
        except discord.HTTPException:
            logger.warning("Gagal edit panel chat order #%s, coba posting ulang.", order_id)

    runtime = RuntimeSettings(db)
    log_channel_id = await runtime.order_log_channel_id()
    if not log_channel_id:
        return False
    channel = bot.get_channel(log_channel_id)
    if not isinstance(channel, discord.TextChannel):
        return False

    try:
        new_message = await channel.send(embed=embed, view=view)
        await order_chat_q.save_panel(db, order_id, channel.id, new_message.id, transcript, latest_image)
        return True
    except discord.HTTPException:
        logger.exception("Gagal posting panel chat baru buat order #%s.", order_id)
        return False
