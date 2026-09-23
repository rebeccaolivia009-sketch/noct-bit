"""
Log aktivitas staff -- diposting ke channel /settings activity_log_channel
biar owner bisa mantau siapa ngapain (perubahan settings, siapa yang
nanganin order, approve/reject kartu, moderasi review) tanpa harus nanya
manual satu-satu.

Best-effort/non-blocking dengan sengaja: kalau channel belum diatur atau
gagal kirim (permission, dst), diem-diem gak ngapa-ngapain -- log ini
CUMA CATETAN, jangan sampe gagal kirim log malah ngeblock aksi aslinya
(order/kartu/setting tetep harus jalan walau log-nya gagal)."""

from __future__ import annotations

import discord

from bot.core.logger import logger
from bot.core.theme import COLOR_MUTED
from bot.ui import embeds
from bot.utils.helpers import RuntimeSettings


async def log_activity(
    bot, actor: discord.abc.User | None, title: str, description: str
) -> None:
    """`actor` itu staff yang ngelakuin aksi-nya -- None kalau emang gak
    ada (misal dipicu sistem otomatis, bukan staff manual)."""
    db = bot.db
    runtime = RuntimeSettings(db)
    channel_id = await runtime.activity_log_channel_id()
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    text = description
    if actor is not None:
        text += f"\n\n-# oleh {actor.mention} ({actor})"
    embed = embeds.base_embed(title, text, color=COLOR_MUTED)
    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        logger.warning("Gagal posting activity log: %s", title)
