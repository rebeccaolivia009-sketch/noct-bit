"""
Command staff: /storestatus -- embed publik "toko lagi buka/tutup" yang
diposting sekali terus DIEDIT IN-PLACE tiap kali staff toggle (bukan kirim
pesan baru tiap kali), biar channel-nya gak kebanjiran histori status lama.

Semuanya MANUAL -- gak ada jadwal/jam operasional otomatis. Staff yang
nentuin sendiri kapan toggle buka/tutup lewat /storestatus open dan
/storestatus close.

Alur pertama kali pake:
  1. /storestatus channel -- pilih channel tempat embed-nya diposting.
  2. (opsional) /storestatus emoji -- ganti emoji indikator dari default
     bulet hijau/merah ke emoji custom server.
  3. /storestatus open atau /storestatus close -- posting embed pertama
     kali sekaligus nentuin state awal.

Abis itu tiap toggle cukup /storestatus open atau /storestatus close --
pesan yang sama keedit, gak nambah pesan baru. Kalau pesannya kehapus
manual di Discord, command berikutnya otomatis posting ulang pesan baru
dan nyimpen ID-nya (self-healing, gak perlu /storestatus channel ulang).
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import settings as settings_q
from bot.ui import embeds
from bot.utils.helpers import RuntimeSettings
from bot.utils.permissions import staff_only
from bot.utils.validators import is_valid_emoji

NOTE_MAX_LENGTH = 200


class StoreStatusCog(commands.Cog):
    """Toggle status buka/tutup toko, ditampilin lewat embed yang di-update di tempat."""

    storestatus_group = app_commands.Group(
        name="storestatus", description="Atur status buka/tutup toko.", guild_only=True
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _refresh_status_message(
        self, interaction: discord.Interaction, state: str, note: str | None
    ) -> discord.TextChannel | None:
        """Post atau edit-in-place embed status di channel yang udah diatur.
        Return channel-nya kalau berhasil, None kalau channel belum diatur
        atau udah gak valid lagi (caller yang tanggung jawab ngasih tau user)."""
        runtime = RuntimeSettings(self.bot.db)
        channel_id = await runtime.store_status_channel_id()
        if not channel_id:
            return None
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return None

        emoji_open = await runtime.store_status_emoji_open()
        emoji_closed = await runtime.store_status_emoji_closed()
        thumbnail_url = await runtime.store_status_thumbnail_url()
        embed = embeds.store_status_embed(state, emoji_open, emoji_closed, note, thumbnail_url)

        message_id = await runtime.store_status_message_id()
        if message_id:
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(embed=embed)
                return channel
            except discord.NotFound:
                # Pesan lama kehapus manual -- fallback posting baru di bawah,
                # ID lama otomatis ketiban ID baru pas disimpen ulang.
                pass
            except discord.HTTPException:
                pass

        sent = await channel.send(embed=embed)
        await settings_q.set_setting(self.bot.db, "store_status_message_id", str(sent.id))
        return channel

    async def _set_state(
        self, interaction: discord.Interaction, state: str, note: str | None
    ) -> None:
        if note and len(note) > NOTE_MAX_LENGTH:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Catatan kepanjangan -- maksimal {NOTE_MAX_LENGTH} karakter."),
                ephemeral=True,
            )
            return

        await settings_q.set_setting(self.bot.db, "store_status_state", state)
        await settings_q.set_setting(self.bot.db, "store_status_note", note or "")

        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = await self._refresh_status_message(interaction, state, note)

        if channel is None:
            await interaction.followup.send(
                embed=embeds.error_embed(
                    "Status udah disimpen, tapi channel-nya belum diatur (atau udah gak valid) -- "
                    "pake `/storestatus channel` dulu biar embed-nya keposting."
                ),
                ephemeral=True,
            )
            return

        label = "BUKA" if state == "open" else "TUTUP"
        await interaction.followup.send(
            embed=embeds.success_embed(f"Status toko diubah jadi **{label}** di {channel.mention}."),
            ephemeral=True,
        )

    @storestatus_group.command(name="open", description="Tandain toko lagi BUKA.")
    @app_commands.describe(catatan="Catatan opsional (misal jam tutup nanti)")
    @staff_only()
    async def open_store(self, interaction: discord.Interaction, catatan: str | None = None) -> None:
        await self._set_state(interaction, "open", catatan)

    @storestatus_group.command(name="close", description="Tandain toko lagi TUTUP.")
    @app_commands.describe(catatan="Catatan opsional (misal jam buka lagi)")
    @staff_only()
    async def close_store(self, interaction: discord.Interaction, catatan: str | None = None) -> None:
        await self._set_state(interaction, "closed", catatan)

    @storestatus_group.command(name="channel", description="Atur channel tempat embed status toko diposting.")
    @app_commands.describe(channel="Channel buat embed status buka/tutup")
    @staff_only()
    async def channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        # Ganti channel -> reset message_id lama, biar command berikutnya
        # posting pesan BARU di channel baru (bukan nyoba edit pesan lama
        # yang sekarang udah beda channel).
        await settings_q.set_setting(self.bot.db, "store_status_channel_id", str(channel.id))
        await settings_q.set_setting(self.bot.db, "store_status_message_id", "")
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Channel status toko diatur ke {channel.mention}. "
                "Pake `/storestatus open` atau `/storestatus close` buat posting embed pertamanya."
            ),
            ephemeral=True,
        )

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

        runtime = RuntimeSettings(self.bot.db)
        state = await runtime.store_status_state()
        note = await runtime.store_status_note()
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._refresh_status_message(interaction, state, note)
        await interaction.followup.send(
            embed=embeds.success_embed(f"Emoji status toko diatur: BUKA {buka} / TUTUP {tutup}."),
            ephemeral=True,
        )

    @storestatus_group.command(name="thumbnail", description="Atur/hapus gambar thumbnail kecil buat embed status toko.")
    @app_commands.describe(image_url="URL gambar thumbnail (PNG/JPG/WebP) -- kosongin buat hapus thumbnail")
    @staff_only()
    async def thumbnail(self, interaction: discord.Interaction, image_url: str | None = None) -> None:
        await settings_q.set_setting(self.bot.db, "store_status_thumbnail_url", image_url or "")

        runtime = RuntimeSettings(self.bot.db)
        state = await runtime.store_status_state()
        note = await runtime.store_status_note()
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._refresh_status_message(interaction, state, note)

        message = "Thumbnail status toko udah diatur." if image_url else "Thumbnail status toko udah dihapus."
        await interaction.followup.send(embed=embeds.success_embed(message), ephemeral=True)

    @storestatus_group.command(name="view", description="Liat pengaturan status toko yang lagi aktif.")
    @staff_only()
    async def view(self, interaction: discord.Interaction) -> None:
        runtime = RuntimeSettings(self.bot.db)
        channel_id = await runtime.store_status_channel_id()
        state = await runtime.store_status_state()
        lines = [
            f"▸ **Status:** {'BUKA' if state == 'open' else 'TUTUP'}",
            f"▸ **Channel:** {f'<#{channel_id}>' if channel_id else 'Belum diatur'}",
            f"▸ **Emoji Buka:** {await runtime.store_status_emoji_open()}",
            f"▸ **Emoji Tutup:** {await runtime.store_status_emoji_closed()}",
            f"▸ **Thumbnail:** {'Diatur' if await runtime.store_status_thumbnail_url() else 'Belum diatur'}",
            f"▸ **Catatan aktif:** {await runtime.store_status_note() or 'Gak ada'}",
        ]
        await interaction.response.send_message(
            embed=embeds.info_embed("Status Toko", "\n".join(lines)), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StoreStatusCog(bot))
