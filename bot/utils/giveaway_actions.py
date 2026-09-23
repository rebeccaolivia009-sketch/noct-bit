"""
Logika ngakhirin giveaway -- dipake BARENG sama dua caller: background task
otomatis pas ends_at kelewat (lihat bot.cogs.tasks) dan command manual
`/giveaway end` / `/giveaway reroll`. Dipisah ke sini (bukan ditulis dobel
di dua tempat) biar pemilihan pemenang, kasih win-role, dan update kartu
giveaway SELALU konsisten lewat jalur manapun giveaway-nya diakhirin.
"""

from __future__ import annotations

import random

import discord

from bot.core.logger import logger
from bot.database.queries import giveaways as giveaways_q
from bot.ui import components


async def end_giveaway(bot, giveaway_id: int, *, reroll: bool = False) -> tuple[bool, str]:
    """Pilih pemenang, kasih win-role, update kartu jadi status berakhir,
    dan umumin di channel. `reroll=True` dipake buat milih ulang pemenang
    giveaway yang UDAH berakhir sebelumnya (gak ngubah status/ends_at lagi,
    cuma nimpa daftar pemenang)."""
    db = bot.db
    giveaway = await giveaways_q.get_giveaway(db, giveaway_id)
    if giveaway is None:
        return False, "Giveaway gak ketemu."
    if not reroll and giveaway["status"] != "active":
        return False, "Giveaway ini udah berakhir sebelumnya."

    entrant_ids = await giveaways_q.list_entrant_ids(db, giveaway_id)
    guild = bot.get_guild(giveaway["guild_id"])

    # Cuma pilih dari member yang MASIH ada di server -- yang udah keluar
    # gak boleh menang meski sempet klik Join sebelum kabur.
    valid_ids = entrant_ids
    if guild is not None:
        valid_ids = [uid for uid in entrant_ids if guild.get_member(uid) is not None]

    winner_count = min(giveaway["winner_count"], len(valid_ids))
    winners = random.sample(valid_ids, winner_count) if winner_count else []

    if reroll:
        await giveaways_q.clear_winners(db, giveaway_id)
    await giveaways_q.record_winners(db, giveaway_id, winners)
    if not reroll:
        await giveaways_q.set_status(db, giveaway_id, "ended")

    # Win-role otomatis ke tiap pemenang, kalau diatur pas /giveaway create.
    if guild is not None and giveaway["win_role_id"]:
        role = guild.get_role(giveaway["win_role_id"])
        if role is not None:
            for uid in winners:
                member = guild.get_member(uid)
                if member is None:
                    continue
                try:
                    await member.add_roles(role, reason=f"Menang giveaway #{giveaway_id}")
                except discord.Forbidden:
                    logger.warning(
                        "Gak punya izin kasih win-role ke %s buat giveaway #%s -- cek posisi role NOCTRA.",
                        uid, giveaway_id,
                    )
                except discord.HTTPException:
                    logger.exception("Gagal kasih win-role ke %s buat giveaway #%s.", uid, giveaway_id)

    # Update kartu giveaway di channel jadi tampilan "berakhir" + hasil,
    # dan umumin pemenangnya lewat pesan biasa (biar nge-ping beneran).
    channel = bot.get_channel(giveaway["channel_id"])
    if isinstance(channel, discord.TextChannel) and giveaway["message_id"]:
        try:
            message = channel.get_partial_message(giveaway["message_id"])
            container = components.giveaway_ended_container(giveaway, winners)
            view = discord.ui.LayoutView(timeout=None)
            view.add_item(container)
            await message.edit(view=view)
        except discord.HTTPException:
            logger.exception("Gagal update kartu giveaway #%s jadi status berakhir.", giveaway_id)

        try:
            if winners:
                mentions = " ".join(f"<@{uid}>" for uid in winners)
                await channel.send(
                    content=(
                        f"\U0001F389 Selamat {mentions}! Kamu menang **{giveaway['prize']}** "
                        f"di giveaway #{giveaway_id}."
                    )
                )
            else:
                await channel.send(
                    content=f"Giveaway #{giveaway_id} berakhir tanpa pemenang -- gak ada peserta yang valid."
                )
        except discord.HTTPException:
            logger.exception("Gagal kirim pengumuman pemenang giveaway #%s.", giveaway_id)

    return True, ("Reroll berhasil, pemenang baru udah diumumin." if reroll else "Giveaway berhasil diakhirin.")
