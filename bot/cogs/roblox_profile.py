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
  1. Staff jalanin /checkprofile username:<username roblox customer>
     [customer:<akun Discord customer-nya, opsional>] di dalem ticket
     order.
  2. Bot balikin preview (ephemeral, cuma staff yang liat) -- cek dulu
     bener apa enggak datanya.
  3. Staff klik "Kirim ke Channel Ini" (posting ke channel yang sama,
     misal ticket) ATAU "Kirim ke DM <nama>" (CUMA muncul kalau
     parameter `customer` diisi) -- kartu yang SAMA dikirim langsung ke
     DM pribadi customer-nya.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.core.logger import logger
from bot.database.queries import settings as settings_q
from bot.ui import components, embeds
from bot.utils.helpers import RuntimeSettings
from bot.utils.permissions import staff_only
from bot.utils.roblox_api import RobloxAPIError, RobloxUserNotFound, fetch_roblox_profile

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")


def _parse_custom_emoji(value: str | None) -> str | None:
    """Validasi emoji -- terima emoji custom SERVER MANA PUN (format
    <:nama:id> / <a:nama:id>) ATAU emoji unicode biasa. Return apa
    adanya (string mentah, BUKAN di-convert ke PartialEmoji -- di sini
    cuma ditempel ke teks TextDisplay, bukan jadi emoji tombol). None
    kalau kosong. Raise ValueError kalau formatnya gak kebaca. Pola sama
    persis kayak helper di bot.cogs.badge/rstock/dst."""
    if not value or not value.strip():
        return None
    value = value.strip()
    try:
        discord.PartialEmoji.from_str(value)  # validasi doang, hasilnya dibuang
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"Format emoji `{value}` gak kebaca. Pake emoji unicode biasa, atau emoji custom "
            "server (ketik `\\:namaemoji:` di chat dulu buat dapet kode aslinya, terus tempel di sini)."
        ) from exc
    return value


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


class SendToChannelButton(discord.ui.Button):
    """Repost kartu yang SAMA (tanpa tombol lagi) ke channel biasa
    (bukan ephemeral) -- biar siapapun di channel itu (misal ticket)
    bisa liat & konfirmasi ini beneran profil mereka."""

    def __init__(self, profile: dict, title_emoji: str) -> None:
        super().__init__(label="Kirim ke Channel Ini", style=discord.ButtonStyle.secondary, emoji="\U0001F4E4")
        self.profile = profile
        self.title_emoji = title_emoji

    async def callback(self, interaction: discord.Interaction) -> None:
        container = components.roblox_profile_container(self.profile, self.title_emoji)
        view = components.NoctraLayout(container, timeout=None)
        try:
            await interaction.channel.send(view=view)
        except discord.HTTPException:
            await interaction.response.send_message(
                embed=embeds.error_embed("Gagal kirim kartu profil ke channel ini."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=embeds.success_embed("Kartu profil udah dikirim ke channel ini."), ephemeral=True
        )


class SendToDMButton(discord.ui.Button):
    """DM kartu yang SAMA langsung ke customer -- CUMA ada kalau staff
    ngisi parameter `customer` pas /checkprofile (bot gak bisa nebak
    sendiri Discord akun mana yang punya username Roblox itu, jadi harus
    staff yang nentuin)."""

    def __init__(self, profile: dict, customer: discord.Member, title_emoji: str) -> None:
        super().__init__(label=f"Kirim ke DM {customer.display_name}", style=discord.ButtonStyle.success, emoji="\U0001F4EC")
        self.profile = profile
        self.customer = customer
        self.title_emoji = title_emoji

    async def callback(self, interaction: discord.Interaction) -> None:
        container = components.roblox_profile_container(self.profile, self.title_emoji)
        view = components.NoctraLayout(container, timeout=None)
        try:
            await self.customer.send(view=view)
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Gak bisa DM {self.customer.mention} -- DM-nya lagi ditutup."),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.response.send_message(
                embed=embeds.error_embed("Gagal kirim DM, coba lagi."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Kartu profil udah di-DM ke {self.customer.mention}."), ephemeral=True
        )


class ProfilePreviewView(discord.ui.LayoutView):
    def __init__(self, profile: dict, customer: discord.Member | None, title_emoji: str) -> None:
        super().__init__(timeout=300)
        container = components.roblox_profile_container(profile, title_emoji)
        buttons = [SendToChannelButton(profile, title_emoji)]
        if customer is not None:
            buttons.append(SendToDMButton(profile, customer, title_emoji))
        container.add_item(discord.ui.ActionRow(*buttons))
        self.add_item(container)


class RobloxProfileCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="checkprofile", description="Cek profil Roblox customer -- data asli dari Roblox API.")
    @app_commands.describe(
        username="Username Roblox customer yang mau dicek",
        customer="Akun Discord customer-nya (opsional) -- isi ini kalau mau bisa kirim hasilnya ke DM dia",
    )
    @app_commands.guild_only()
    @staff_only()
    async def checkprofile(
        self, interaction: discord.Interaction, username: str, customer: discord.Member | None = None
    ) -> None:
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

        title_emoji = await RuntimeSettings(self.bot.db).roblox_profile_title_emoji()
        await interaction.followup.send(view=ProfilePreviewView(profile, customer, title_emoji), ephemeral=True)

    @app_commands.command(name="checkprofile_emoji", description="Atur emoji di judul kartu /checkprofile (boleh emoji custom server).")
    @app_commands.describe(emoji="Emoji buat judul kartu")
    @app_commands.guild_only()
    @staff_only()
    async def checkprofile_emoji(self, interaction: discord.Interaction, emoji: str) -> None:
        try:
            parsed = _parse_custom_emoji(emoji)
        except ValueError as exc:
            await interaction.response.send_message(embed=embeds.error_embed(str(exc)), ephemeral=True)
            return
        if parsed is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Emoji-nya gak boleh kosong."), ephemeral=True
            )
            return

        await settings_q.set_setting(self.bot.db, "roblox_profile_title_emoji", parsed)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Emoji judul kartu /checkprofile diatur ke {parsed}."), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RobloxProfileCog(bot))
