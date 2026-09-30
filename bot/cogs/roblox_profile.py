"""
Command staff: /checkprofile -- cek profil Roblox customer (username,
display name, User ID, tanggal dibuat akun, avatar) LANGSUNG dari Roblox
API publik, bukan checker abal-abal. Dipake buat mastiin username yang
customer kasih emang beneran akun mereka sebelum staff proses order
Robux.

SATU keterbatasan yang GAK BISA diakalin dengan cara apapun: saldo Robux
akun ORANG LAIN gak pernah bisa dicek dari luar -- Roblox cuma expose
saldo ke akun pemiliknya sendiri (butuh cookie auth punya akun itu).
Field ini sengaja ditulis "gak bisa dicek" di kartunya, BUKAN angka
ngasal yang keliatan meyakinkan padahal karangan -- lihat
bot.utils.roblox_api buat penjelasan lengkapnya.

Alur pemakaian:
  1. Staff jalanin /checkprofile username:<username roblox customer> di
     dalem ticket order.
  2. Bot balikin preview (ephemeral, cuma staff yang liat) -- cek dulu
     bener apa enggak datanya.
  3. Staff klik "Kirim ke Customer" -- kartu yang SAMA diposting ke
     channel (keliatan customer), biar mereka bisa konfirmasi itu profil
     mereka.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.core.logger import logger
from bot.ui import components, embeds
from bot.utils.permissions import staff_only
from bot.utils.roblox_api import RobloxAPIError, RobloxUserNotFound, fetch_roblox_profile

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


def _format_created(iso_str: str | None) -> tuple[str, str]:
    """Return (tanggal dibaca manusia, umur akun) dari timestamp ISO 8601
    yang Roblox balikin -- misal ("17 Apr 2013", "12 tahun lalu")."""
    if not iso_str:
        return "?", ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return iso_str, ""
    display = dt.strftime("%d %b %Y")
    days = (datetime.now(timezone.utc) - dt).days
    years = days // 365
    age = f"{years} tahun lalu" if years >= 1 else f"{max(days, 0)} hari lalu"
    return display, age


class SendProfileButton(discord.ui.Button):
    """Repost kartu yang SAMA (tanpa tombol ini lagi) ke channel biasa
    (bukan ephemeral) -- biar customer di ticket itu bisa liat & konfirmasi
    ini beneran profil mereka."""

    def __init__(self, profile: dict) -> None:
        super().__init__(label="Kirim ke Customer", style=discord.ButtonStyle.success, emoji="\U0001F4E4")
        self.profile = profile

    async def callback(self, interaction: discord.Interaction) -> None:
        container = components.roblox_profile_container(self.profile)
        view = components.NoctraLayout(container, timeout=None)
        try:
            await interaction.channel.send(view=view)
        except discord.HTTPException:
            await interaction.response.send_message(
                embed=embeds.error_embed("Gagal kirim kartu profil ke channel ini."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=embeds.success_embed("Kartu profil udah dikirim ke channel ini -- minta customer konfirmasi."),
            ephemeral=True,
        )


class ProfilePreviewView(discord.ui.LayoutView):
    def __init__(self, profile: dict) -> None:
        super().__init__(timeout=300)
        container = components.roblox_profile_container(profile)
        container.add_item(discord.ui.ActionRow(SendProfileButton(profile)))
        self.add_item(container)


class RobloxProfileCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="checkprofile", description="Cek profil Roblox customer -- data asli dari Roblox API.")
    @app_commands.describe(username="Username Roblox customer yang mau dicek")
    @app_commands.guild_only()
    @staff_only()
    async def checkprofile(self, interaction: discord.Interaction, username: str) -> None:
        username = username.strip().lstrip("@")
        if not _USERNAME_RE.match(username):
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Username Roblox harus 3-20 karakter, huruf/angka/underscore doang."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            raw_profile = await fetch_roblox_profile(username)
        except RobloxUserNotFound:
            await interaction.followup.send(
                embed=embeds.error_embed(f"Username Roblox **{username}** gak ketemu."), ephemeral=True
            )
            return
        except RobloxAPIError as exc:
            logger.warning("Roblox API error buat /checkprofile (%s): %s", username, exc)
            await interaction.followup.send(
                embed=embeds.error_embed(
                    "Roblox API-nya lagi gak bisa diakses -- coba lagi beberapa saat lagi."
                ),
                ephemeral=True,
            )
            return

        created_display, account_age = _format_created(raw_profile.get("created"))
        profile = {**raw_profile, "created_display": created_display, "account_age_display": account_age}

        await interaction.followup.send(view=ProfilePreviewView(profile), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RobloxProfileCog(bot))
