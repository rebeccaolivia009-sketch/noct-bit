"""
Command staff: /giveaway.

Giveaway dengan tombol "Join" persistent (custom_id dinamis, restart-safe --
sama triknya kayak OrderActionButton/CardRequestActionButton di
bot.ui.views), warna & emoji tombol bisa diatur bebas pas /giveaway create,
dan auto-pick pemenang begitu waktu abis (lihat GiveawayTasks di
bot.cogs.tasks). Pemenang otomatis dikasih role hadiah (win_role) kalau
diatur, gak perlu staff assign manual satu-satu.

Publik: tombol Join (GiveawayJoinButton, bot.ui.views) -- siapa aja klik
buat masuk/keluar entry, gak butuh permission apapun, dan kartu giveaway-nya
otomatis ke-update jumlah pesertanya real-time tiap ada yang klik.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import giveaways as giveaways_q
from bot.core.theme import COLOR_ACCENT
from bot.ui import components, embeds
from bot.ui.views import GiveawayJoinButton, GiveawayView
from bot.utils import giveaway_actions
from bot.utils.permissions import staff_only
from bot.utils.validators import parse_hex_color

_DURATION_RE = re.compile(
    r"^(?:(?P<days>\d+)d)?(?:(?P<hours>\d+)h)?(?:(?P<minutes>\d+)m)?$", re.IGNORECASE
)


def _parse_duration_minutes(text: str) -> int | None:
    """Parse durasi kayak '1d12h30m', '2h', '45m' jadi total menit. Return
    None kalau formatnya gak valid atau hasilnya 0 (misal user ngetik
    string kosong / cuma huruf)."""
    cleaned = text.strip().lower().replace(" ", "")
    match = _DURATION_RE.fullmatch(cleaned)
    if not match:
        return None
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    total = days * 1440 + hours * 60 + minutes
    return total or None


class GiveawayCog(commands.Cog):
    """Bikin dan kelola giveaway."""

    giveaway_group = app_commands.Group(
        name="giveaway", description="Bikin dan kelola giveaway.", guild_only=True
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @giveaway_group.command(name="create", description="Bikin giveaway baru.")
    @app_commands.describe(
        channel="Channel tempat giveaway diposting",
        prize="Hadiah giveaway",
        durasi="Durasi giveaway, misal 1d12h, 2h, 45m",
        pemenang="Jumlah pemenang (default 1)",
        judul="Judul giveaway (opsional, boleh pake emoji custom server)",
        deskripsi="Deskripsi giveaway (opsional, boleh pake emoji custom server)",
        win_role="Role yang otomatis kepasang ke pemenang (opsional)",
        label_tombol="Teks tombol Join (opsional, default 'Ikut Giveaway')",
        warna_tombol="Warna tombol Join",
        emoji_tombol="Emoji tombol Join -- custom server atau unicode biasa (opsional)",
        warna_aksen="Warna aksen kartu giveaway, kode hex (opsional, misal #7C5CFF)",
    )
    @app_commands.choices(
        warna_tombol=[
            app_commands.Choice(name="Biru (Primary)", value="primary"),
            app_commands.Choice(name="Abu-abu (Secondary)", value="secondary"),
            app_commands.Choice(name="Hijau (Success)", value="success"),
            app_commands.Choice(name="Merah (Danger)", value="danger"),
        ]
    )
    @staff_only()
    async def create(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        prize: str,
        durasi: str,
        pemenang: app_commands.Range[int, 1, 50] = 1,
        judul: str | None = None,
        deskripsi: str | None = None,
        win_role: discord.Role | None = None,
        label_tombol: str | None = None,
        warna_tombol: app_commands.Choice[str] | None = None,
        emoji_tombol: str | None = None,
        warna_aksen: str | None = None,
    ) -> None:
        minutes = _parse_duration_minutes(durasi)
        if minutes is None:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Format durasi gak valid. Contoh yang bener: `1d12h`, `2h`, `45m`."
                ),
                ephemeral=True,
            )
            return

        emoji: discord.PartialEmoji | None = None
        if emoji_tombol:
            try:
                emoji = discord.PartialEmoji.from_str(emoji_tombol.strip())
            except Exception:  # noqa: BLE001
                await interaction.response.send_message(
                    embed=embeds.error_embed(
                        "Emoji tombol gak valid. Pake emoji custom server atau emoji unicode biasa."
                    ),
                    ephemeral=True,
                )
                return

        color = COLOR_ACCENT
        if warna_aksen:
            parsed_color, error = parse_hex_color(warna_aksen, COLOR_ACCENT)
            if error:
                await interaction.response.send_message(embed=embeds.error_embed(error), ephemeral=True)
                return
            color = parsed_color

        if win_role is not None:
            bot_member = interaction.guild.me if interaction.guild else None
            if bot_member and win_role.position >= bot_member.top_role.position:
                await interaction.response.send_message(
                    embed=embeds.error_embed(
                        f"Posisi role {win_role.mention} sejajar atau di atas role tertinggi NOCTRA -- "
                        "naikin dulu posisi role NOCTRA di **Server Settings > Roles** biar bisa "
                        "di-assign otomatis ke pemenang nanti."
                    ),
                    ephemeral=True,
                )
                return

        ends_at = (discord.utils.utcnow() + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")

        db = self.bot.db
        giveaway_id = await giveaways_q.create_giveaway(
            db,
            guild_id=interaction.guild_id,
            channel_id=channel.id,
            host_user_id=interaction.user.id,
            title=judul or "\U0001F389 GIVEAWAY \U0001F389",
            description=deskripsi,
            prize=prize,
            winner_count=pemenang,
            win_role_id=win_role.id if win_role else None,
            button_label=(label_tombol or "Ikut Giveaway")[:80],
            button_style=(warna_tombol.value if warna_tombol else "primary"),
            button_emoji=str(emoji) if emoji else None,
            color=color,
            ends_at=ends_at,
        )
        giveaway = await giveaways_q.get_giveaway(db, giveaway_id)

        container = components.giveaway_container(giveaway, 0)
        join_button = GiveawayJoinButton.from_giveaway_row(giveaway)
        view = GiveawayView(container, join_button)

        try:
            message = await channel.send(view=view)
        except discord.HTTPException:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Gagal kirim giveaway ke channel itu -- cek izin NOCTRA buat kirim pesan di sana."
                ),
                ephemeral=True,
            )
            return

        await giveaways_q.set_message_id(db, giveaway_id, message.id)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Giveaway #{giveaway_id} berhasil dibuat di {channel.mention}."),
            ephemeral=True,
        )

    @giveaway_group.command(name="end", description="Akhirin giveaway lebih cepet & pilih pemenang sekarang.")
    @app_commands.describe(giveaway_id="ID giveaway yang mau diakhirin")
    @staff_only()
    async def end(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, message = await giveaway_actions.end_giveaway(self.bot, giveaway_id)
        await interaction.followup.send(
            embed=embeds.success_embed(message) if ok else embeds.error_embed(message), ephemeral=True
        )

    @giveaway_group.command(name="reroll", description="Pilih ulang pemenang giveaway yang udah berakhir.")
    @app_commands.describe(giveaway_id="ID giveaway yang mau di-reroll")
    @staff_only()
    async def reroll(self, interaction: discord.Interaction, giveaway_id: int) -> None:
        giveaway = await giveaways_q.get_giveaway(self.bot.db, giveaway_id)
        if giveaway is None:
            await interaction.response.send_message(embed=embeds.error_embed("Giveaway gak ketemu."), ephemeral=True)
            return
        if giveaway["status"] != "ended":
            await interaction.response.send_message(
                embed=embeds.error_embed("Cuma giveaway yang udah berakhir yang bisa di-reroll."),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, message = await giveaway_actions.end_giveaway(self.bot, giveaway_id, reroll=True)
        await interaction.followup.send(
            embed=embeds.success_embed(message) if ok else embeds.error_embed(message), ephemeral=True
        )

    @giveaway_group.command(name="list", description="Liat giveaway yang lagi aktif di server ini.")
    @staff_only()
    async def list_giveaways(self, interaction: discord.Interaction) -> None:
        db = self.bot.db
        active = await giveaways_q.list_active_giveaways(db, interaction.guild_id)
        if not active:
            await interaction.response.send_message(
                embed=embeds.info_embed("Giveaway Aktif", "Gak ada giveaway yang lagi jalan di server ini."),
                ephemeral=True,
            )
            return

        lines = []
        for g in active:
            count = await giveaways_q.count_entries(db, g["id"])
            ends_ts = int(datetime.fromisoformat(g["ends_at"]).timestamp())
            lines.append(
                f"**#{g['id']} -- {g['title']}**\n"
                f"Hadiah: {g['prize']} {chr(0x2022)} Peserta: {count} {chr(0x2022)} "
                f"Berakhir: <t:{ends_ts}:R>"
            )
        await interaction.response.send_message(
            embed=embeds.info_embed("Giveaway Aktif", "\n\n".join(lines)), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GiveawayCog(bot))
