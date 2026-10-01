"""
Status bot "Watching ..." yang gonta-ganti otomatis tiap 15 detik --
daftar teksnya disimpen di bot_presence_statuses, diisi SEKALI pake
daftar default pas tabelnya masih kosong (lihat cog_load), abis itu bisa
diubah staff lewat /presence add|remove|list tanpa ke-reset tiap bot
restart.

Interval rotasi diatur di SATU tempat doang -- angka di decorator
@tasks.loop di bawah (default 15 detik). Naikin/turunin tinggal ganti
angka itu, gak ada tempat lain yang perlu disentuh.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.database.queries import presence as presence_q
from bot.ui import embeds
from bot.utils.permissions import staff_only

DEFAULT_STATUSES = [
    "1000+ Rating",
    "10.000 Produk Terjual",
    "NOCTRA DIGITAL STORE",
    "Automatic Order",
    "FAST RESPON",
    "\u00A9 Since 2025",
    "Sell All Digital Produk",
    "Multi Payment Methods",
    "The One And Only",
]

ROTATE_SECONDS = 15


class PresenceCog(commands.Cog):
    """Rotasi status 'Watching ...' bot + command buat atur daftarnya."""

    presence_group = app_commands.Group(
        name="presence", description="Atur daftar status 'Watching ...' bot yang gonta-ganti otomatis.",
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._index = 0
        self.rotate_loop.start()

    async def cog_load(self) -> None:
        await presence_q.seed_if_empty(self.bot.db, DEFAULT_STATUSES)

    async def cog_unload(self) -> None:
        self.rotate_loop.cancel()

    @tasks.loop(seconds=ROTATE_SECONDS)
    async def rotate_loop(self) -> None:
        statuses = await presence_q.list_statuses(self.bot.db)
        if not statuses:
            return
        self._index %= len(statuses)
        text = statuses[self._index]["text"]
        self._index += 1

        try:
            await self.bot.change_presence(
                activity=discord.Activity(type=discord.ActivityType.watching, name=text)
            )
        except discord.HTTPException:
            pass

    @rotate_loop.before_loop
    async def before_rotate_loop(self) -> None:
        await self.bot.wait_until_ready()

    # -- Command staff ---------------------------------------------------------

    @presence_group.command(name="add", description="Tambahin teks status 'Watching ...' baru ke daftar rotasi.")
    @app_commands.describe(teks="Teks status baru -- misal 'FAST RESPON'")
    @staff_only()
    async def add(self, interaction: discord.Interaction, teks: str) -> None:
        await presence_q.add_status(self.bot.db, teks)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Status **{teks}** ditambahin ke daftar rotasi."), ephemeral=True
        )

    @presence_group.command(name="remove", description="Hapus satu status dari daftar rotasi.")
    @app_commands.describe(nomor="Nomor urut status yang mau dihapus (liat di /presence list)")
    @staff_only()
    async def remove(self, interaction: discord.Interaction, nomor: int) -> None:
        removed = await presence_q.remove_status_at(self.bot.db, nomor - 1)
        if not removed:
            await interaction.response.send_message(
                embed=embeds.error_embed("Nomor gak valid -- cek lagi urutannya di `/presence list`."),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=embeds.success_embed("Status dihapus."), ephemeral=True)

    @presence_group.command(name="list", description="Liat semua status 'Watching ...' yang lagi dirotasi.")
    @staff_only()
    async def list_statuses_cmd(self, interaction: discord.Interaction) -> None:
        statuses = await presence_q.list_statuses(self.bot.db)
        if not statuses:
            await interaction.response.send_message(
                embed=embeds.info_embed("Status Bot", "Belum ada status di daftar rotasi."), ephemeral=True
            )
            return
        lines = [f"`{i + 1}.` {s['text']}" for i, s in enumerate(statuses)]
        await interaction.response.send_message(
            embed=embeds.info_embed(
                f"Status Bot (rotasi tiap {ROTATE_SECONDS} detik)", "\n".join(lines)
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PresenceCog(bot))
