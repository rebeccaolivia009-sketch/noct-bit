"""
Auto Reaction: bot otomatis nambahin reaksi emoji ke pesan baru di channel
yang dipilih staff (/autoreact set -> panel dengan pratinjau).

Aturan engine:
  * Filter pesan: semua pesan / cuma yang ada gambar-lampiran / cuma yang ada link.
  * Pesan bot diabaikan (bisa dimatiin), dan pesan bot ini sendiri SELALU
    diabaikan (biar sticky/pengumuman sendiri gak ikut diberi reaksi).
  * Pesan sistem (join, pin, thread) diabaikan.
  * Emoji ditambahin berurutan. Emoji yang rusak/dihapus dilewatin, pesan
    yang keburu dihapus atau reaksi udah penuh (20) menghentikan sisanya,
    izin kurang dicatat di log (maksimal sekali per beberapa menit per channel
    biar log gak banjir).
  * Channel yang dipantau di-cache di memori -- pesan di channel biasa gak
    nyentuh database.
"""

from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

from bot.core.logger import logger
from bot.database.queries import auto_reactions as auto_reactions_q
from bot.ui import embeds
from bot.ui.autoreact import AutoReactPanel
from bot.utils import autoreact_spec as spec
from bot.utils.permissions import staff_only

# Kode error Discord yang artinya spesifik (bukan masalah umum)
CODE_UNKNOWN_MESSAGE = 10008
CODE_UNKNOWN_EMOJI = 10014
CODE_REACTION_BLOCKED = 90001
CODE_MAX_REACTIONS = 30010


class AutoReactCog(commands.Cog):
    """Reaksi emoji otomatis per channel."""

    WARN_INTERVAL = 300.0   # detik minimal antar peringatan log per channel
    MAX_PENDING = 50        # batas pesan antre reaksi per channel (pelindung kalau channel banjir)

    autoreact_group = app_commands.Group(
        name="autoreact",
        description="Kelola auto reaction (reaksi emoji otomatis di sebuah channel).",
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._cfg: dict[int, dict] = {}
        self._pending: dict[int, int] = {}
        self._last_warn: dict[int, float] = {}

    async def cog_load(self) -> None:
        self._cfg = {}
        for row in await auto_reactions_q.list_enabled(self.bot.db):
            self._remember(row)
        logger.info("Auto reaction: %d channel dipantau.", len(self._cfg))

    def _remember(self, row) -> None:
        emojis = auto_reactions_q.emojis_of(row)
        if emojis and row["enabled"]:
            self._cfg[int(row["channel_id"])] = {
                "emojis": emojis, "mode": row["filter_mode"], "ignore_bots": bool(row["ignore_bots"]),
            }
        else:
            self._cfg.pop(int(row["channel_id"]), None)

    async def apply(self, channel_id: int) -> None:
        """Sinkronin cache dengan data tersimpan (dipanggil panel pas Simpan)."""
        row = await auto_reactions_q.get_by_channel(self.bot.db, channel_id)
        if row is None:
            self._cfg.pop(channel_id, None)
        else:
            self._remember(row)

    async def remove(self, channel_id: int) -> bool:
        existed = await auto_reactions_q.get_by_channel(self.bot.db, channel_id) is not None
        await auto_reactions_q.delete(self.bot.db, channel_id)
        self._cfg.pop(channel_id, None)
        return existed

    # -- listener ---------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        cfg = self._cfg.get(message.channel.id)
        if cfg is None:
            return  # channel biasa -- nol kerja, nol query
        if self.bot.user is not None and message.author.id == self.bot.user.id:
            return
        if message.author.bot and cfg["ignore_bots"]:
            return
        if message.is_system():
            return
        has_media = bool(message.attachments) or any(
            e.image or e.video or e.thumbnail for e in message.embeds
        )
        if not spec.matches(cfg["mode"], message.content, has_media):
            return
        channel_id = message.channel.id
        if self._pending.get(channel_id, 0) >= self.MAX_PENDING:
            return  # channel lagi banjir -- lewatin daripada numpuk antrean
        self._pending[channel_id] = self._pending.get(channel_id, 0) + 1
        try:
            await self._react(message, cfg["emojis"])
        finally:
            self._pending[channel_id] -= 1

    def _warn(self, channel_id: int, text: str, *args) -> None:
        now = time.monotonic()
        if now - self._last_warn.get(channel_id, float("-inf")) >= self.WARN_INTERVAL:
            self._last_warn[channel_id] = now
            logger.warning(text, *args)

    async def _react(self, message: discord.Message, emojis: list[str]) -> None:
        for emoji in emojis:
            try:
                await message.add_reaction(emoji)
            except discord.Forbidden as exc:
                if exc.code == CODE_REACTION_BLOCKED:
                    return  # pengirim pesan memblokir bot -- gak bisa direaksiin
                self._warn(
                    message.channel.id,
                    "Auto reaction: bot gak bisa nambah reaksi di channel %s (cek izin Add Reactions / Read Message History).",
                    message.channel.id,
                )
                return
            except discord.NotFound as exc:
                if exc.code == CODE_UNKNOWN_EMOJI:
                    self._warn(message.channel.id, "Auto reaction: emoji %s gak dikenal Discord (dihapus?) di channel %s.", emoji, message.channel.id)
                    continue
                return  # pesan keburu dihapus
            except discord.HTTPException as exc:
                if exc.code == CODE_MAX_REACTIONS:
                    return  # pesan udah penuh reaksi
                if exc.code == CODE_UNKNOWN_EMOJI:
                    self._warn(message.channel.id, "Auto reaction: emoji %s gak dikenal Discord di channel %s.", emoji, message.channel.id)
                    continue
                self._warn(message.channel.id, "Auto reaction: Discord error di channel %s: %s", message.channel.id, exc)
                return

    # -- command ------------------------------------------------------------------

    @autoreact_group.command(name="set", description="Buka panel buat bikin/edit auto reaction di sebuah channel.")
    @app_commands.describe(channel="Channel yang pesannya mau dikasih reaksi otomatis")
    @staff_only()
    async def set_autoreact(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        row = await auto_reactions_q.get_by_channel(self.bot.db, channel.id)
        panel = AutoReactPanel(interaction.user.id, channel, row)
        await interaction.response.send_message(view=await panel.render(self.bot), ephemeral=True)

    @autoreact_group.command(name="list", description="Liat semua auto reaction di server ini.")
    @staff_only()
    async def list_autoreact(self, interaction: discord.Interaction) -> None:
        rows = await auto_reactions_q.list_guild(self.bot.db, interaction.guild.id)
        if not rows:
            await interaction.response.send_message(
                embed=embeds.info_embed("Auto Reaction", "Belum ada. Bikin lewat `/autoreact set`."), ephemeral=True
            )
            return
        lines = []
        for r in rows:
            state = "aktif" if r["enabled"] else "\u23F8\uFE0F nonaktif"
            emojis = " ".join(auto_reactions_q.emojis_of(r)) or "-"
            lines.append(f"<#{r['channel_id']}> \u2014 {emojis} \u2014 {spec.MODE_SHORT.get(r['filter_mode'], r['filter_mode'])} \u2014 {state}")
        await interaction.response.send_message(
            embed=embeds.info_embed("Auto Reaction", "\n".join(lines)), ephemeral=True
        )

    @autoreact_group.command(name="remove", description="Hapus auto reaction di sebuah channel.")
    @app_commands.describe(channel="Channel yang auto reaction-nya mau dihapus")
    @staff_only()
    async def remove_autoreact(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        removed = await self.remove(channel.id)
        embed = (
            embeds.success_embed(f"Auto reaction di {channel.mention} dihapus.")
            if removed else embeds.error_embed("Channel itu gak punya auto reaction.")
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoReactCog(bot))
