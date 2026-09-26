"""Hitung status buka/tutup toko OTOMATIS dari jam operasional (bukan
toggle manual staff lagi) + refresh panel Components V2-nya di channel
yang diatur (/storestatus channel). Dipake bareng dari bot.cogs.
store_status (tiap ada perubahan setting -- refresh instan) dan
bot.cogs.store_status_task (loop background -- ngecek jam jalan terus).

WIB di-hardcode sebagai OFFSET TETAP (UTC+7), BUKAN lewat zoneinfo/
Asia/Jakarta -- WIB gak pernah DST jadi ini akurat 100%, dan ini sengaja
ngehindarin ketergantungan ke paket tzdata sistem yang gak dijamin
ke-install di image Railway/Nixpacks (pelajaran yang sama kayak kenapa
font di-bundle manual di leaderboard_image.py, bukan pake font sistem)."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import discord

from bot.core.logger import logger
from bot.database.queries import settings as settings_q
from bot.ui import components
from bot.utils.helpers import RuntimeSettings

WIB = timezone(timedelta(hours=7))


def _parse_hhmm(value: str) -> time:
    hour_str, _, minute_str = value.partition(":")
    return time(int(hour_str), int(minute_str))


def compute_state(open_time: str, close_time: str, now: datetime | None = None) -> str:
    """Return "open" atau "closed" berdasarkan jam sekarang (WIB) vs jam
    operasional yang diatur. Nanganin jam operasional yang NGELEWATIN
    tengah malam (misal buka 09:00, tutup 02:00) -- BUKAN dianggep "jam
    buka harus lebih kecil dari jam tutup"."""
    now = (now or datetime.now(WIB)).astimezone(WIB)
    current = now.time()
    open_t = _parse_hhmm(open_time)
    close_t = _parse_hhmm(close_time)

    if open_t == close_t:
        return "open"  # buka == tutup -- dianggep 24 jam, gak ada jeda tutup sama sekali
    if open_t < close_t:
        is_open = open_t <= current < close_t
    else:
        # Ngelewatin tengah malam -- "buka" kalau jam sekarang >= jam buka
        # ATAU < jam tutup (rentangnya muter, bukan "di antara" biasa).
        is_open = current >= open_t or current < close_t
    return "open" if is_open else "closed"


async def refresh_store_status(bot) -> bool:
    """Hitung ulang state dari jam operasional, simpen ke settings (cache
    buat /storestatus view), terus edit-in-place (atau posting baru kalau
    belum ada / pesan lama kehapus -- self-healing) panel di channel yang
    diatur. Return False kalau channel-nya belum diatur atau udah gak
    valid -- caller (command) yang tanggung jawab ngasih tau staff soal
    itu, loop background (bot.cogs.store_status_task) diem-diem doang."""
    db = bot.db
    runtime = RuntimeSettings(db)

    open_time = await runtime.store_status_open_time()
    close_time = await runtime.store_status_close_time()
    state = compute_state(open_time, close_time)
    await settings_q.set_setting(db, "store_status_state", state)

    channel_id = await runtime.store_status_channel_id()
    if not channel_id:
        return False
    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return False

    container = components.store_status_container(
        state, open_time, close_time,
        await runtime.store_status_emoji_open(), await runtime.store_status_emoji_closed(),
        await runtime.store_status_note(),
        await runtime.store_status_banner_url(), await runtime.store_status_thumbnail_url(),
        ping_role_id=await runtime.store_status_ping_role_id(),
    )
    view = components.NoctraLayout(container, timeout=None)

    message_id = await runtime.store_status_message_id()
    if message_id:
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(view=view)
            return True
        except discord.NotFound:
            pass  # pesan lama kehapus manual -- fallback posting baru di bawah
        except discord.HTTPException:
            logger.exception("Gagal edit panel status toko.")
            return False

    try:
        sent = await channel.send(view=view)
        await settings_q.set_setting(db, "store_status_message_id", str(sent.id))
        return True
    except discord.HTTPException:
        logger.exception("Gagal posting panel status toko.")
        return False
