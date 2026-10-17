"""
Command: /stats (customer) dan /stats_panel (staff).

/stats          -- kartu statistik pembelian kamu: peringkat leaderboard,
                   total pesanan, total spend, dengan gaya yang makin
                   eksklusif seiring belanja (lihat bot.utils.stats_style).
                   Defaultnya cuma keliatan kamu; `publik:True` buat pamer.
/stats_panel    -- posting panel publik berisi tombol "Cek Statistik Saya"
                   biar customer gak perlu ngetik command.

Tombol "Riwayat Pesanan" di kartu ngirim riwayat ke channel yang sama
(keliatan semua orang) dan cuma bisa dipencet sama pemilik kartu.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.ui import embeds
from bot.ui.stats import StatsHistoryButton, StatsOpenButton, build_entry_view, send_card
from bot.utils.permissions import staff_only


class StatsCog(commands.Cog):
    """Kartu statistik pembelian customer."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        # Tombol persisten: custom_id-nya dikenali lagi setelah bot restart.
        self.bot.add_dynamic_items(StatsHistoryButton, StatsOpenButton)

    async def cog_unload(self) -> None:
        self.bot.remove_dynamic_items(StatsHistoryButton, StatsOpenButton)

    @app_commands.command(name="stats", description="Liat kartu statistik pembelian: peringkat, total pesanan, dan total spend.")
    @app_commands.describe(
        member="Liat kartu member lain (default: kamu sendiri)",
        publik="Tampilkan ke semua orang di channel (default: cuma kamu yang liat)",
    )
    @app_commands.guild_only()
    async def stats(
        self, interaction: discord.Interaction, member: discord.Member | None = None, publik: bool = False
    ) -> None:
        await send_card(interaction, member or interaction.user, ephemeral=not publik)

    @app_commands.command(name="stats_panel", description="Posting panel 'Cek Statistik Saya' di sebuah channel.")
    @app_commands.describe(channel="Channel tempat panel diposting")
    @app_commands.guild_only()
    @staff_only()
    async def stats_panel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        try:
            await channel.send(view=build_entry_view())
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Bot gak punya izin kirim pesan di {channel.mention}."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Panel statistik diposting di {channel.mention}."), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatsCog(bot))
