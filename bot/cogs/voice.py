"""
Command admin: /voice join | /voice leave -- bot nongkrong (AFK) di voice
channel. Gak muterin audio apapun, cuma join terus diem.

Channel tujuan disimpen di DB (tabel `settings`), jadi bot otomatis masuk
lagi abis restart/redeploy, dan dijaga tetep di channel itu:
  * di-kick/di-disconnect dari voice  -> join ulang
  * dipindahin staff ke channel lain  -> channel tujuan ikut diganti
  * connection nyangkut/putus diam-diam -> watchdog (cek tiap 2 menit)
    nyambungin ulang
`/voice leave` ngehapus channel tujuan, jadi bot gak bakal join lagi.

Butuh `discord.py[voice]` (PyNaCl + davey) -- lihat requirements.txt.
"""

from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord import voice_client as _voice_client

from bot.core.logger import logger
from bot.database.queries import voice_settings as voice_settings_q
from bot.ui import embeds
from bot.utils.permissions import staff_only

# Jeda sebelum join ulang abis kedeteksi ke-disconnect -- ngasih waktu buat
# discord.py nyoba reconnect sendiri (kasus jaringan putus sesaat) biar kita
# gak rebutan sama dia.
REJOIN_DELAY_SECONDS = 5


class VoiceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._locks: dict[int, asyncio.Lock] = {}

    # -- Lifecycle ---------------------------------------------------------

    async def cog_load(self) -> None:
        if not _voice_client.has_nacl:
            logger.warning("PyNaCl gak ke-install -- fitur /voice gak bakal bisa join. Pasang `discord.py[voice]`.")
        if not getattr(_voice_client, "has_dave", False):
            logger.warning("Paket `davey` gak ke-install -- join voice bisa gagal. Pasang `discord.py[voice]`.")
        self.watchdog.start()

    async def cog_unload(self) -> None:
        self.watchdog.cancel()

    def _lock(self, guild_id: int) -> asyncio.Lock:
        return self._locks.setdefault(guild_id, asyncio.Lock())

    # -- Inti: pastiin bot nyambung ke channel tujuan ------------------------

    async def _ensure_connected(self, guild: discord.Guild, channel_id: int) -> None:
        """Bikin bot nyambung ke `channel_id`. Aman dipanggil berkali-kali:
        kalau udah nyambung & di channel yang bener, gak ngapa-ngapain.
        Raise kalau gagal nyambung (caller yang mutusin mau diapain)."""
        async with self._lock(guild.id):
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.VoiceChannel):
                # Channel-nya kehapus (atau bukan voice lagi) -- percuma
                # dicoba terus, buang setting-nya.
                await voice_settings_q.clear_voice_channel(self.bot.db, guild.id)  # type: ignore[attr-defined]
                logger.warning("Voice channel %s di guild %s gak ketemu -- setting AFK dihapus.", channel_id, guild.id)
                return

            perms = channel.permissions_for(guild.me)
            if not (perms.view_channel and perms.connect):
                raise PermissionError(f"Bot gak punya izin View Channel + Connect di {channel.mention}.")

            voice = guild.voice_client
            if voice is not None and voice.is_connected():
                if voice.channel is None or voice.channel.id != channel.id:
                    await voice.move_to(channel)
                return
            if voice is not None:
                # Sisa koneksi basi -- bersihin dulu sebelum konek ulang.
                await voice.disconnect(force=True)

            await channel.connect(self_deaf=True, reconnect=True)
            logger.info("Join voice channel %s di guild %s.", channel.id, guild.id)

    # -- Watchdog (juga jadi auto-rejoin abis restart) -----------------------

    @tasks.loop(minutes=2)
    async def watchdog(self) -> None:
        # Iterasi PERTAMA jalan begitu bot ready -- itu yang bikin bot
        # otomatis masuk voice lagi abis restart/redeploy.
        try:
            entries = await voice_settings_q.list_voice_channels(self.bot.db)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            logger.exception("Voice watchdog: gagal baca setting dari DB.")
            return
        for guild_id, channel_id in entries:
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            try:
                await self._ensure_connected(guild, channel_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Voice watchdog: gagal nyambung ke channel %s (guild %s): %s", channel_id, guild_id, exc)

    @watchdog.before_loop
    async def _before_watchdog(self) -> None:
        await self.bot.wait_until_ready()

    # -- Jaga posisi bot -----------------------------------------------------

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if self.bot.user is None or member.id != self.bot.user.id or self.bot.is_closed():
            return
        try:
            stored = await voice_settings_q.get_voice_channel(self.bot.db, member.guild.id)  # type: ignore[attr-defined]
            if stored is None:
                return  # gak lagi mode AFK (misal abis /voice leave) -- biarin

            if after.channel is not None:
                # Dipindahin ke channel lain (misal sama staff) -- ikutin,
                # jangan malah ditarik balik ke channel lama.
                if after.channel.id != stored:
                    await voice_settings_q.set_voice_channel(self.bot.db, member.guild.id, after.channel.id)  # type: ignore[attr-defined]
                return

            # Keluar dari voice (di-kick/di-disconnect) -- coba masuk lagi.
            await asyncio.sleep(REJOIN_DELAY_SECONDS)
            if self.bot.is_closed():
                return
            stored = await voice_settings_q.get_voice_channel(self.bot.db, member.guild.id)  # type: ignore[attr-defined]
            if stored is None:
                return  # /voice leave dijalanin selama jeda itu
            await self._ensure_connected(member.guild, stored)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Voice: gagal jaga posisi di guild %s: %s", member.guild.id, exc)

    # -- Commands --------------------------------------------------------------

    voice = app_commands.Group(
        name="voice", description="Atur bot nongkrong (AFK) di voice channel.", guild_only=True
    )

    @voice.command(name="join", description="Bot join ke voice channel dan standby di sana (AFK).")
    @app_commands.describe(channel="Voice channel tujuan -- kosongin buat pake voice channel tempat kamu lagi berada")
    @staff_only()
    async def voice_join(self, interaction: discord.Interaction, channel: discord.VoiceChannel | None = None) -> None:
        assert interaction.guild is not None
        if channel is None:
            voice_state = interaction.user.voice if isinstance(interaction.user, discord.Member) else None
            if voice_state is not None and isinstance(voice_state.channel, discord.VoiceChannel):
                channel = voice_state.channel
            else:
                await interaction.response.send_message(
                    embed=embeds.error_embed(
                        "Pilih voice channel-nya di parameter `channel`, atau masuk ke voice channel dulu "
                        "biar bot tau mau join ke mana."
                    ),
                    ephemeral=True,
                )
                return
        assert channel is not None

        await interaction.response.defer(ephemeral=True)
        try:
            await self._ensure_connected(interaction.guild, channel.id)
        except PermissionError as exc:
            await interaction.followup.send(embed=embeds.error_embed(str(exc)), ephemeral=True)
            return
        except (asyncio.TimeoutError, discord.ClientException, discord.HTTPException) as exc:
            logger.warning("Voice join gagal di guild %s: %s", interaction.guild.id, exc)
            await interaction.followup.send(
                embed=embeds.error_embed(
                    f"Gagal join {channel.mention}: {str(exc) or type(exc).__name__}. "
                    "Cek channel-nya gak penuh, dan bot punya izin Connect di situ."
                ),
                ephemeral=True,
            )
            return

        # Simpen SETELAH berhasil konek -- biar channel yang gagal gak kesimpen.
        await voice_settings_q.set_voice_channel(self.bot.db, interaction.guild.id, channel.id)  # type: ignore[attr-defined]
        await interaction.followup.send(
            embed=embeds.success_embed(
                f"Bot udah standby di {channel.mention}. Bakal otomatis masuk lagi kalau bot restart "
                "atau ke-disconnect -- pake `/voice leave` buat nyuruh keluar."
            ),
            ephemeral=True,
        )

    @voice.command(name="leave", description="Bot keluar dari voice channel dan berhenti AFK.")
    @staff_only()
    async def voice_leave(self, interaction: discord.Interaction) -> None:
        assert interaction.guild is not None
        stored = await voice_settings_q.get_voice_channel(self.bot.db, interaction.guild.id)  # type: ignore[attr-defined]
        voice = interaction.guild.voice_client
        if stored is None and voice is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Bot lagi gak ada di voice channel manapun."), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        # Hapus setting DULUAN -- biar listener on_voice_state_update & watchdog
        # gak nganggep disconnect ini sebagai "ke-kick" terus join lagi.
        await voice_settings_q.clear_voice_channel(self.bot.db, interaction.guild.id)  # type: ignore[attr-defined]
        if voice is not None:
            try:
                await voice.disconnect(force=True)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Voice leave: gagal disconnect bersih di guild %s: %s", interaction.guild.id, exc)
        await interaction.followup.send(
            embed=embeds.success_embed("Bot udah keluar dari voice channel dan gak bakal join lagi sendiri."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VoiceCog(bot))
