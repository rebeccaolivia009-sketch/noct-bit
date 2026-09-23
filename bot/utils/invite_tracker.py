"""Posts or refreshes the invite tracker leaderboard image in the
configured channel. Pola SAMA PERSIS kayak bot.utils.leaderboard.py
(Top Spenders) -- edit-in-place pesan yang lagi aktif, fallback repost
kalau pesannya udah kehapus -- BEDA cuma di sumber data (invite_members,
per-guild) dan generator gambarnya (invite_leaderboard_image, gaya list
minimalis, bukan podium)."""

from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO

import aiohttp
import discord

from bot.core.logger import logger
from bot.database.queries import invites as invites_q
from bot.utils.helpers import RuntimeSettings
from bot.utils.invite_leaderboard_image import generate_invite_leaderboard_image


async def _fetch_avatar(session: aiohttp.ClientSession, url: str):
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
            if resp.status == 200:
                data = await resp.read()
                from PIL import Image
                return Image.open(BytesIO(data)).convert("RGBA")
    except Exception:
        pass
    return None


async def refresh_invite_leaderboard(bot) -> bool:
    db = bot.db
    runtime = RuntimeSettings(db)

    channel_id = await runtime.invite_leaderboard_channel_id()
    if not channel_id:
        return False
    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return False

    # Leaderboard invite tracker per-GUILD (beda dari Top Spenders yang
    # satu-toko-global) -- guild-nya diambil dari channel leaderboard-nya
    # sendiri, soalnya jumlah invite emang inherently per-server.
    guild = channel.guild
    rows = await invites_q.get_leaderboard(db, guild.id, limit=10)
    if not rows:
        return False

    entries = []
    async with aiohttp.ClientSession() as session:
        for i, row in enumerate(rows):
            display_name = f"User {row['inviter_id']}"
            avatar_img = None
            try:
                user = bot.get_user(row["inviter_id"]) or await bot.fetch_user(row["inviter_id"])
                display_name = user.display_name
                avatar_img = await _fetch_avatar(session, str(user.display_avatar.url))
            except Exception:
                pass
            entries.append({
                "rank": i,
                "display_name": display_name,
                "total_invites": row["total_invites"],
                "avatar": avatar_img,
            })

    ts = datetime.now(timezone.utc).strftime("Updated %d %b %Y, %H:%M UTC")
    buf = generate_invite_leaderboard_image(entries, timestamp=ts)

    existing_id = await invites_q.get_leaderboard_message_id(db, guild.id)
    if existing_id:
        try:
            msg = await channel.fetch_message(existing_id)
            buf.seek(0)
            await msg.edit(attachments=[discord.File(buf, filename="invite_leaderboard.png")])
            logger.info("Leaderboard invite di-refresh (edit pesan %s) buat guild %s.", existing_id, guild.id)
            return True
        except discord.NotFound:
            pass
        except discord.HTTPException:
            logger.exception("Gagal edit leaderboard invite, posting ulang.")

    try:
        buf.seek(0)
        new_msg = await channel.send(file=discord.File(buf, filename="invite_leaderboard.png"))
        await invites_q.set_leaderboard_message_id(db, guild.id, new_msg.id)
        logger.info("Leaderboard invite diposting (pesan baru %s) buat guild %s.", new_msg.id, guild.id)
        return True
    except discord.HTTPException:
        logger.exception("Gagal posting leaderboard invite.")
        return False
