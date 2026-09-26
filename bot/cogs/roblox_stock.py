"""
Auto-respon embed stock ROBUX -- member ketik "rstock" (persis, case-
insensitive, harus jadi ISI PESAN itu doang -- bukan cuma disebut di
tengah kalimat) di channel manapun, bot langsung bales panel stock (3
tipe: via Username, via Login, Gamepass).

Dua pengaman anti-spam:
  * Auto kehapus abis 2 menit (delete_after) -- biar channel gak numpuk
    histori panel lama tiap kali di-trigger.
  * Jeda 10 detik PER CHANNEL antar respon -- kalau ada yang ketik
    "rstock" lagi sebelum jeda abis, bot DIEM AJA (gak nge-panel lagi,
    gak ngasih pesan error/peringatan juga -- itu sendiri bisa jadi
    sasaran spam kalau dibales).

Isi stock (teks bebas per tipe) & emoji custom-nya diatur staff lewat
/rstock stock dan /rstock emoji -- SEMUA bagian (judul, tiap tipe,
footer) WAJIB ada emoji, makanya semuanya punya default kalau staff
belum sempet atur (lihat RuntimeSettings.rstock_emoji_*).
"""

from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import settings as settings_q
from bot.ui import components, embeds
from bot.utils.helpers import RuntimeSettings
from bot.utils.permissions import staff_only

TRIGGER_KEYWORD = "rstock"
COOLDOWN_SECONDS = 10
AUTO_DELETE_SECONDS = 120


def _parse_custom_emoji(value: str | None) -> str | None:
    """Validasi emoji -- terima emoji custom SERVER MANA PUN (format
    <:nama:id> / <a:nama:id>) ATAU emoji unicode biasa. Return apa adanya
    (disimpen sebagai string mentah, BUKAN di-convert ke PartialEmoji --
    di sini cuma ditempel ke teks TextDisplay, bukan jadi emoji tombol).
    None kalau kosong (caller pake default). Raise ValueError kalau
    formatnya gak kebaca sama sekali."""
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


class RobloxStockCog(commands.Cog):
    """Auto-respon panel stock ROBUX -- trigger kata kunci 'rstock'."""

    rstock_group = app_commands.Group(
        name="rstock", description="Atur isi & emoji auto-respon stock ROBUX (trigger kata kunci: rstock).",
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # {channel_id: waktu respon terakhir} -- in-memory doang, cukup
        # buat jeda 10 detik, gak perlu disimpen ke DB (reset pas bot
        # restart itu gak masalah sama sekali buat jeda sesingkat ini).
        self._last_response: dict[int, float] = {}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        if message.content.strip().lower() != TRIGGER_KEYWORD:
            return

        now = time.monotonic()
        last = self._last_response.get(message.channel.id, 0.0)
        if now - last < COOLDOWN_SECONDS:
            return
        self._last_response[message.channel.id] = now

        view = await self._build_view()
        try:
            await message.channel.send(view=view, delete_after=AUTO_DELETE_SECONDS)
        except discord.HTTPException:
            pass

    async def _build_view(self) -> discord.ui.LayoutView:
        runtime = RuntimeSettings(self.bot.db)
        container = components.rstock_container(
            await runtime.rstock_emoji_title(),
            await runtime.rstock_emoji_via_username(), await runtime.rstock_info_via_username(),
            await runtime.rstock_emoji_via_login(), await runtime.rstock_info_via_login(),
            await runtime.rstock_emoji_gamepass(), await runtime.rstock_info_gamepass(),
            await runtime.rstock_emoji_footer(),
        )
        return components.NoctraLayout(container, timeout=None)

    # -- Command staff --------------------------------------------------------

    @rstock_group.command(name="stock", description="Atur isi stock ROBUX per tipe (kosongin parameter yang gak diubah).")
    @app_commands.describe(
        via_username="Isi stock/harga buat tipe Via Username",
        via_login="Isi stock/harga buat tipe Via Login",
        gamepass="Isi stock/harga buat tipe Gamepass",
    )
    @staff_only()
    async def stock(
        self, interaction: discord.Interaction,
        via_username: str | None = None, via_login: str | None = None, gamepass: str | None = None,
    ) -> None:
        if via_username is not None:
            await settings_q.set_setting(self.bot.db, "rstock_info_via_username", via_username)
        if via_login is not None:
            await settings_q.set_setting(self.bot.db, "rstock_info_via_login", via_login)
        if gamepass is not None:
            await settings_q.set_setting(self.bot.db, "rstock_info_gamepass", gamepass)
        await interaction.response.send_message(embed=embeds.success_embed("Stock ROBUX diupdate."), ephemeral=True)

    @rstock_group.command(name="emoji", description="Atur emoji custom di tiap bagian panel (kosongin yang gak diubah).")
    @app_commands.describe(
        title="Emoji di judul panel",
        via_username="Emoji buat tipe Via Username",
        via_login="Emoji buat tipe Via Login",
        gamepass="Emoji buat tipe Gamepass",
        footer="Emoji di footer",
    )
    @staff_only()
    async def emoji(
        self, interaction: discord.Interaction,
        title: str | None = None, via_username: str | None = None, via_login: str | None = None,
        gamepass: str | None = None, footer: str | None = None,
    ) -> None:
        try:
            parsed = {
                "rstock_emoji_title": _parse_custom_emoji(title),
                "rstock_emoji_via_username": _parse_custom_emoji(via_username),
                "rstock_emoji_via_login": _parse_custom_emoji(via_login),
                "rstock_emoji_gamepass": _parse_custom_emoji(gamepass),
                "rstock_emoji_footer": _parse_custom_emoji(footer),
            }
        except ValueError as exc:
            await interaction.response.send_message(embed=embeds.error_embed(str(exc)), ephemeral=True)
            return

        for key, value in parsed.items():
            if value is not None:
                await settings_q.set_setting(self.bot.db, key, value)
        await interaction.response.send_message(embed=embeds.success_embed("Emoji panel stock ROBUX diupdate."), ephemeral=True)

    @rstock_group.command(name="view", description="Liat isi & emoji panel stock ROBUX yang lagi aktif.")
    @staff_only()
    async def view(self, interaction: discord.Interaction) -> None:
        runtime = RuntimeSettings(self.bot.db)
        lines = [
            f"\u25b8 **Judul:** {await runtime.rstock_emoji_title()} STOCK ROBUX",
            f"\u25b8 **Via Username:** {await runtime.rstock_emoji_via_username()} {await runtime.rstock_info_via_username()}",
            f"\u25b8 **Via Login:** {await runtime.rstock_emoji_via_login()} {await runtime.rstock_info_via_login()}",
            f"\u25b8 **Gamepass:** {await runtime.rstock_emoji_gamepass()} {await runtime.rstock_info_gamepass()}",
            f"\u25b8 **Footer:** {await runtime.rstock_emoji_footer()}",
            f"\u25b8 **Trigger:** `{TRIGGER_KEYWORD}` (persis, case-insensitive) -- jeda {COOLDOWN_SECONDS} detik/channel, auto-hapus {AUTO_DELETE_SECONDS // 60} menit.",
        ]
        await interaction.response.send_message(
            embed=embeds.info_embed("Stock ROBUX (rstock)", "\n".join(lines)), ephemeral=True
        )

    @rstock_group.command(name="test", description="Kirim contoh panel stock ROBUX di channel ini (gak kena jeda 10 detik).")
    @staff_only()
    async def test(self, interaction: discord.Interaction) -> None:
        view = await self._build_view()
        try:
            await interaction.channel.send(view=view, delete_after=AUTO_DELETE_SECONDS)
        except discord.HTTPException:
            await interaction.response.send_message(embed=embeds.error_embed("Gagal kirim contoh panel."), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=embeds.success_embed("Contoh panel stock ROBUX udah dikirim di channel ini (bakal kehapus otomatis abis 2 menit)."),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RobloxStockCog(bot))
