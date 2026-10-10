"""
Command staff: /tradecheck -- checker akun Roblox buat jual-beli limited.
Ngecek apakah akun pembeli memenuhi syarat trade: inventory publik/privat,
total RAP & Value, item tumbal (limited termurah), dan -- kalau ROBLOX_COOKIE
diatur -- verifikasi resmi dari Roblox soal izin trade (Plus/Premium dll).
Logika & aturan verdict: bot.utils.roblox_trade_check.

Subcommand:
  /tradecheck cek username:<username/link profil>  -- cek satu akun (hasil
        ephemeral, ada tombol "Kirim ke Channel Ini" buat ngepost kartu
        versi pembeli ke ticket).
  /tradecheck panel  -- posting panel publik "Cek Akun Trade" di channel ini
        (misal di channel katalog Roblox) -- pembeli ngecek akunnya sendiri
        lewat tombol, hasilnya ephemeral.
  /tradecheck debug username:<...>  -- respons MENTAH tiap sumber (Roblox,
        Rolimons, trades). Jalanin ini PERTAMA KALI di server asli buat
        mastiin semua sumber jalan -- endpoint trades itu cookie-auth/legacy
        dan formatnya bisa berubah tanpa pemberitahuan.

Tombol "Cek Akun Saya" juga bisa ditempel ke baris tombol panel katalog
Roblox yang udah ada: bot.ui.roblox_check_view.RobloxCheckPanelButton().
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.core.logger import logger
from bot.ui import embeds
from bot.ui.roblox_check_view import build_panel_view, perform_check
from bot.utils.permissions import staff_only
from bot.utils.roblox_trade_check import parse_roblox_identifier, run_diagnostics


class RobloxCheckCog(commands.Cog):
    tradecheck_group = app_commands.Group(
        name="tradecheck",
        description="Checker akun Roblox buat jual-beli limited (syarat trade, RAP, Value, item tumbal).",
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @tradecheck_group.command(name="cek", description="Cek akun Roblox pembeli: syarat trade, RAP, Value, item tumbal.")
    @app_commands.describe(username="Username Roblox, link profil (roblox.com/users/.../profile), atau id:<angka>")
    @staff_only()
    async def cek(self, interaction: discord.Interaction, username: str) -> None:
        try:
            kind, value = parse_roblox_identifier(username)
        except ValueError as exc:
            await interaction.response.send_message(embed=embeds.error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await perform_check(interaction, kind, value, post_to_channel=True)

    @tradecheck_group.command(name="panel", description="Posting panel publik 'Cek Akun Trade' di channel ini.")
    @staff_only()
    async def panel(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.channel.send(view=build_panel_view())
        except discord.HTTPException:
            logger.exception("Gagal posting panel trade checker.")
            await interaction.response.send_message(
                embed=embeds.error_embed("Gagal posting panel di channel ini."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=embeds.success_embed("Panel Cek Akun Trade udah diposting di channel ini."), ephemeral=True
        )

    @tradecheck_group.command(name="debug", description="Respons mentah tiap sumber data (buat validasi pertama kali).")
    @app_commands.describe(username="Username Roblox, link profil, atau id:<angka>")
    @staff_only()
    async def debug(self, interaction: discord.Interaction, username: str) -> None:
        try:
            kind, value = parse_roblox_identifier(username)
        except ValueError as exc:
            await interaction.response.send_message(embed=embeds.error_embed(str(exc)), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        lines = await run_diagnostics(kind, value)
        await interaction.followup.send(
            embed=embeds.info_embed("Trade Checker Debug", "\n".join(f"\u25b8 {line}" for line in lines)[:4000]),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RobloxCheckCog(bot))
