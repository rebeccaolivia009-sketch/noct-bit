"""
Command staff: /storestatus -- panel Components V2 publik "toko lagi
buka/tutup" yang diposting sekali terus DIEDIT IN-PLACE (bukan kirim
pesan baru tiap kali), biar channel-nya gak kebanjiran histori status
lama.

BEDA dari versi sebelumnya: status buka/tutup sekarang OTOMATIS dihitung
dari jam operasional (/storestatus jam_operasional), BUKAN toggle manual
staff lagi -- lihat bot.utils.store_status.compute_state (dijalanin
bareng lewat loop bot.cogs.store_status_task tiap 60 detik) dan
bot.utils.store_status.refresh_store_status (dipanggil instan tiap staff
ubah pengaturan di sini).

Alur pertama kali pake:
  1. /storestatus channel -- pilih channel tempat panelnya diposting.
  2. /storestatus jam_operasional -- atur jam buka & tutup (WIB), SEKALIAN
     posting panel pertama kalinya kalau channel-nya udah diatur.
  3. (opsional) /storestatus emoji / banner / thumbnail / note -- custom
     tampilannya.

Kalau pesannya kehapus manual di Discord, refresh berikutnya (baik dari
command di sini ATAU dari loop background) otomatis posting ulang pesan
baru dan nyimpen ID-nya (self-healing, gak perlu /storestatus channel
ulang).
"""

from __future__ import annotations

import re

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import settings as settings_q
from bot.ui import embeds
from bot.utils.helpers import RuntimeSettings
from bot.utils.permissions import staff_only
from bot.utils.store_status import notify_state_ping, refresh_store_status
from bot.utils.validators import is_valid_emoji

NOTE_MAX_LENGTH = 200
_HHMM_RE = re.compile(r"^([01]?[0-9]|2[0-3]):([0-5][0-9])$")


def _valid_hhmm(value: str) -> str | None:
    """Return jam yang udah dinormalisasi ('9:5' -> '09:05') kalau
    formatnya valid, None kalau enggak."""
    match = _HHMM_RE.match(value.strip())
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{match.group(2)}"


class StoreStatusCog(commands.Cog):
    """Panel status buka/tutup toko -- otomatis ngikutin jam operasional."""

    storestatus_group = app_commands.Group(
        name="storestatus", description="Atur panel status toko (buka/tutup otomatis sesuai jam operasional).",
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _refresh_and_reply(self, interaction: discord.Interaction, success_text: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok = await refresh_store_status(self.bot)
        if ok:
            await interaction.followup.send(embed=embeds.success_embed(success_text), ephemeral=True)
        else:
            await interaction.followup.send(
                embed=embeds.error_embed(
                    "Pengaturan udah disimpen, tapi channel-nya belum diatur (atau udah gak valid) -- "
                    "pake `/storestatus channel` dulu biar panelnya keposting."
                ),
                ephemeral=True,
            )

    @storestatus_group.command(name="jam_operasional", description="Atur jam buka & tutup toko (WIB) -- status BUKA/TUTUP ngikutin ini otomatis.")
    @app_commands.describe(
        buka="Jam buka, format 24 jam HH:MM (WIB) -- misal 09:00",
        tutup="Jam tutup, format 24 jam HH:MM (WIB) -- boleh lebih kecil dari jam buka buat ngelewatin tengah malam, misal 02:00",
    )
    @staff_only()
    async def jam_operasional(self, interaction: discord.Interaction, buka: str, tutup: str) -> None:
        open_time = _valid_hhmm(buka)
        close_time = _valid_hhmm(tutup)
        if not open_time or not close_time:
            await interaction.response.send_message(
                embed=embeds.error_embed("Format jam harus HH:MM (24 jam), misal `09:00` atau `22:30`."),
                ephemeral=True,
            )
            return

        runtime = RuntimeSettings(self.bot.db)
        previous_state = await runtime.store_status_state()

        await settings_q.set_setting(self.bot.db, "store_status_open_time", open_time)
        await settings_q.set_setting(self.bot.db, "store_status_close_time", close_time)

        await interaction.response.defer(ephemeral=True, thinking=True)
        ok = await refresh_store_status(self.bot)
        if not ok:
            await interaction.followup.send(
                embed=embeds.error_embed(
                    "Jam operasional udah disimpen, tapi channel-nya belum diatur (atau udah gak valid) -- "
                    "pake `/storestatus channel` dulu biar panelnya keposting."
                ),
                ephemeral=True,
            )
            return

        # Jam baru ini bisa aja LANGSUNG ngubah status toko saat ini juga
        # (bukan cuma buat nanti) -- kalau iya, ping-nya jangan nunggu
        # loop background 60 detik lagi, langsung aja sekarang.
        new_state = await RuntimeSettings(self.bot.db).store_status_state()
        if new_state != previous_state:
            await notify_state_ping(self.bot, new_state)

        await interaction.followup.send(
            embed=embeds.success_embed(
                f"Jam operasional diatur: **{open_time} - {close_time} WIB**. Status toko ngikutin ini otomatis."
            ),
            ephemeral=True,
        )

    @storestatus_group.command(name="role", description="Atur role yang di-ping tiap toko buka/tutup otomatis.")
    @app_commands.describe(role="Role yang mau di-ping (kosongin buat matiin ping sama sekali)")
    @staff_only()
    async def role(self, interaction: discord.Interaction, role: discord.Role | None = None) -> None:
        await settings_q.set_setting(self.bot.db, "store_status_ping_role_id", str(role.id) if role else "")
        message = (
            f"Role yang di-ping tiap toko buka/tutup diatur ke {role.mention}."
            if role else "Ping role status toko dimatiin -- panel tetep ke-update, cuma gak ada ping lagi."
        )
        await interaction.response.send_message(embed=embeds.success_embed(message), ephemeral=True)

    @storestatus_group.command(name="channel", description="Atur channel tempat panel status toko diposting.")
    @app_commands.describe(channel="Channel buat panel status buka/tutup")
    @staff_only()
    async def channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        # Ganti channel -> reset message_id lama, biar refresh berikutnya
        # posting pesan BARU di channel baru (bukan nyoba edit pesan lama
        # yang sekarang udah beda channel).
        await settings_q.set_setting(self.bot.db, "store_status_channel_id", str(channel.id))
        await settings_q.set_setting(self.bot.db, "store_status_message_id", "")
        await self._refresh_and_reply(interaction, f"Channel status toko diatur ke {channel.mention}.")

    @storestatus_group.command(name="emoji", description="Atur emoji custom buat indikator BUKA dan TUTUP.")
    @app_commands.describe(
        buka="Emoji buat status BUKA (unicode atau custom server, misal <:online:123...>)",
        tutup="Emoji buat status TUTUP (unicode atau custom server)",
    )
    @staff_only()
    async def emoji(self, interaction: discord.Interaction, buka: str, tutup: str) -> None:
        if not is_valid_emoji(buka):
            await interaction.response.send_message(
                embed=embeds.error_embed(f"`{buka}` bukan emoji yang valid."), ephemeral=True
            )
            return
        if not is_valid_emoji(tutup):
            await interaction.response.send_message(
                embed=embeds.error_embed(f"`{tutup}` bukan emoji yang valid."), ephemeral=True
            )
            return

        await settings_q.set_setting(self.bot.db, "store_status_emoji_open", buka)
        await settings_q.set_setting(self.bot.db, "store_status_emoji_closed", tutup)
        await self._refresh_and_reply(interaction, f"Emoji status toko diatur: BUKA {buka} / TUTUP {tutup}.")

    @storestatus_group.command(name="banner", description="Atur/hapus gambar banner full-width paling atas panel status toko.")
    @app_commands.describe(image_url="URL gambar banner (PNG/JPG/WebP) -- kosongin buat hapus banner")
    @staff_only()
    async def banner(self, interaction: discord.Interaction, image_url: str | None = None) -> None:
        await settings_q.set_setting(self.bot.db, "store_status_banner_url", image_url or "")
        message = "Banner status toko udah diatur." if image_url else "Banner status toko udah dihapus."
        await self._refresh_and_reply(interaction, message)

    @storestatus_group.command(name="thumbnail", description="Atur/hapus gambar thumbnail kecil di footer panel status toko.")
    @app_commands.describe(image_url="URL gambar thumbnail (PNG/JPG/WebP) -- kosongin buat hapus thumbnail")
    @staff_only()
    async def thumbnail(self, interaction: discord.Interaction, image_url: str | None = None) -> None:
        await settings_q.set_setting(self.bot.db, "store_status_thumbnail_url", image_url or "")
        message = "Thumbnail status toko udah diatur." if image_url else "Thumbnail status toko udah dihapus."
        await self._refresh_and_reply(interaction, message)

    @storestatus_group.command(name="note", description="Atur/hapus catatan tambahan di bawah status (misal pengumuman libur).")
    @app_commands.describe(catatan="Teks catatan -- kosongin buat hapus")
    @staff_only()
    async def note(self, interaction: discord.Interaction, catatan: str | None = None) -> None:
        if catatan and len(catatan) > NOTE_MAX_LENGTH:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Catatan kepanjangan -- maksimal {NOTE_MAX_LENGTH} karakter."),
                ephemeral=True,
            )
            return
        await settings_q.set_setting(self.bot.db, "store_status_note", catatan or "")
        message = "Catatan status toko udah diatur." if catatan else "Catatan status toko udah dihapus."
        await self._refresh_and_reply(interaction, message)

    @storestatus_group.command(name="view", description="Liat pengaturan status toko yang lagi aktif.")
    @staff_only()
    async def view(self, interaction: discord.Interaction) -> None:
        runtime = RuntimeSettings(self.bot.db)
        channel_id = await runtime.store_status_channel_id()
        state = await runtime.store_status_state()
        lines = [
            f"\u25b8 **Status sekarang:** {'BUKA' if state == 'open' else 'TUTUP'} (otomatis)",
            f"\u25b8 **Jam Operasional:** {await runtime.store_status_open_time()} - {await runtime.store_status_close_time()} WIB",
            f"\u25b8 **Channel:** {f'<#{channel_id}>' if channel_id else 'Belum diatur'}",
            f"\u25b8 **Role Ping:** {f'<@&{await runtime.store_status_ping_role_id()}>' if await runtime.store_status_ping_role_id() else 'Gak ada'}",
            f"\u25b8 **Emoji Buka:** {await runtime.store_status_emoji_open()}",
            f"\u25b8 **Emoji Tutup:** {await runtime.store_status_emoji_closed()}",
            f"\u25b8 **Banner:** {'Diatur' if await runtime.store_status_banner_url() else 'Belum diatur'}",
            f"\u25b8 **Thumbnail:** {'Diatur' if await runtime.store_status_thumbnail_url() else 'Belum diatur'}",
            f"\u25b8 **Catatan aktif:** {await runtime.store_status_note() or 'Gak ada'}",
        ]
        await interaction.response.send_message(
            embed=embeds.info_embed("Status Toko", "\n".join(lines)), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StoreStatusCog(bot))
