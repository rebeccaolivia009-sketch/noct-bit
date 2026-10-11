"""Guard blacklist buat alur order: user yang ada di tabel `blacklist` gak
bisa mulai order. Dipasang di bot.ui.views.proceed_to_fields (titik paling
awal alur order, sebelum modal/pembayaran) -- lihat bot.cogs.blacklist buat
command pengelolaannya.

FAIL-OPEN: kalau pengecekan blacklist sendiri error (database bermasalah
dll), order TETEP diizinkan dan error-nya dicatat -- fitur keamanan
tambahan gak boleh sampai ngeblokir penjualan normal."""

from __future__ import annotations

import time

import discord

from bot.core.logger import logger
from bot.database.queries import blacklist as blacklist_q
from bot.ui import embeds
from bot.utils.helpers import RuntimeSettings

ALERT_COOLDOWN_SECONDS = 600.0
_last_alert: dict[int, float] = {}


async def is_blacklisted(db, user_id: int) -> bool:
    try:
        return await blacklist_q.get_entry(db, user_id) is not None
    except Exception:  # noqa: BLE001
        logger.exception("Gagal cek blacklist buat user %s -- order tetep diizinkan.", user_id)
        return False


async def guard_order(interaction: discord.Interaction) -> bool:
    """True = user-nya DIBLOKIR (pesan penolakan udah dikirim, pemanggil
    wajib berhenti). False = lanjut normal.

    Pembeli cuma dikasih tau umum (alasan blacklist internal, gak dibuka).
    Staff dikabarin lewat channel order-log, dibatasi 1x per 10 menit per
    user biar gak banjir kalau dia klik berkali-kali."""
    db = interaction.client.db  # type: ignore[attr-defined]
    if not await is_blacklisted(db, interaction.user.id):
        return False

    notice = embeds.error_embed(
        "Maaf, akun kamu saat ini tidak bisa melakukan order di toko ini. "
        "Kalau kamu merasa ini keliru, hubungi staff."
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=notice, ephemeral=False)
        else:
            await interaction.response.send_message(embed=notice, ephemeral=False)
    except discord.HTTPException:
        logger.warning("Gagal kirim pesan penolakan blacklist ke user %s.", interaction.user.id)

    await _alert_staff(interaction)
    return True


async def _alert_staff(interaction: discord.Interaction) -> None:
    now = time.monotonic()
    if now - _last_alert.get(interaction.user.id, 0.0) < ALERT_COOLDOWN_SECONDS:
        return
    _last_alert[interaction.user.id] = now
    try:
        log_channel_id = await RuntimeSettings(interaction.client.db).order_log_channel_id()  # type: ignore[attr-defined]
        channel = interaction.client.get_channel(log_channel_id) if log_channel_id else None
        if isinstance(channel, discord.TextChannel):
            await channel.send(
                embed=embeds.error_embed(
                    f"Percobaan order DIBLOKIR: <@{interaction.user.id}> (`{interaction.user.id}`) ada di blacklist."
                ),
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except Exception:  # noqa: BLE001
        logger.warning("Gagal kabarin staff soal percobaan order dari user blacklist.")


def buyer_standing(stats: dict, blacklisted: bool) -> dict:
    """Status kepercayaan pembeli dari RIWAYAT ORDER (aturan transparan,
    bukan skor misterius) -- cuma buat staff:

      DIBLOKIR         ada di blacklist
      PERLU PERHATIAN  refund >= 2, ATAU (>= 3 order selesai diproses dan
                       lebih dari 50% berakhir batal/refund)
      TRUSTED BUYER    >= 5 order selesai dan rasio batal+refund <= 20%
      PEMBELI AKTIF    >= 1 order selesai
      PEMBELI BARU     belum pernah ada order selesai

    Rasio = (batal + refund) / (selesai + batal + refund). Order yang
    kedaluwarsa karena belum dibayar dilaporin terpisah, gak masuk rasio."""
    completed, cancelled, refunded = stats["completed"], stats["cancelled"], stats["refunded"]
    bad = cancelled + refunded
    finished = completed + bad
    ratio = (bad / finished) if finished else 0.0

    if blacklisted:
        return {"key": "blacklisted", "label": "DIBLOKIR", "summary": "User ini ada di blacklist.", "ratio": ratio}
    if refunded >= 2 or (finished >= 3 and ratio > 0.5):
        return {
            "key": "attention", "label": "PERLU PERHATIAN", "ratio": ratio,
            "summary": "Banyak order berakhir batal/refund. Cek riwayatnya sebelum memproses order besar.",
        }
    if completed >= 5 and ratio <= 0.2:
        return {
            "key": "trusted", "label": "TRUSTED BUYER", "ratio": ratio,
            "summary": f"{completed} order selesai dengan rasio batal/refund rendah.",
        }
    if completed >= 1:
        return {
            "key": "active", "label": "PEMBELI AKTIF", "ratio": ratio,
            "summary": f"{completed} order selesai, belum cukup riwayat untuk status Trusted.",
        }
    return {"key": "new", "label": "PEMBELI BARU", "summary": "Belum pernah ada order yang selesai.", "ratio": ratio}
