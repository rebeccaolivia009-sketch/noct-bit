"""
Command staff: /badge.

Badge custom leaderboard -- CUMA berlaku buat user yang lagi di top 1-3
Top Spenders. User yang berhak (WAJIB punya role dari /badge role) atur
badge-nya sendiri (teks + gradient 2 warna) lewat panel Components V2 di
channel yang staff tentuin (/badge channel) -- lihat bot.ui.views.
BadgePanelView & BadgeSetModal. Badge-nya digambar langsung ke PNG
leaderboard (bot.utils.leaderboard_image), bukan komponen Discord.

Background leaderboard (gambar custom, misal logo/icon store) juga diatur
di sini lewat /badge background.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import leaderboard as lb_q
from bot.database.queries import settings as settings_q
from bot.ui import embeds
from bot.ui.views import BadgePanelView
from bot.utils.permissions import staff_only


def _parse_custom_emoji(value: str | None) -> discord.PartialEmoji | None:
    """Validasi emoji buat tombol panel -- terima emoji custom SERVER MANA
    PUN (format <:nama:id> / <a:nama:id>, didapet dari ngetik `\\:nama:` di
    chat Discord lalu di-copy hasilnya) ATAU emoji unicode biasa. Return
    None kalau kosong (caller pake default bawaan "\U0001F3F7"/
    "\U0001F5D1"). Raise ValueError kalau formatnya gak kebaca sama sekali,
    biar caller bisa kasih tau staff format yang bener."""
    if not value or not value.strip():
        return None
    value = value.strip()
    try:
        return discord.PartialEmoji.from_str(value)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"Format emoji `{value}` gak kebaca. Pake emoji unicode biasa, atau emoji custom "
            "server (ketik `\\:namaemoji:` di chat dulu buat dapet kode aslinya, terus tempel di sini)."
        ) from exc


class BadgeCog(commands.Cog):
    """Atur badge custom leaderboard & background-nya."""

    badge_group = app_commands.Group(
        name="badge", description="Atur badge custom leaderboard & backgroundnya.", guild_only=True
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @badge_group.command(name="role", description="Atur role yang wajib dipunya buat bisa custom badge.")
    @app_commands.describe(role="Role yang wajib dipunya (biasanya role khusus top leaderboard)")
    @staff_only()
    async def role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await settings_q.set_setting(self.bot.db, "leaderboard_badge_role_id", str(role.id))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Role buat custom badge diatur ke {role.mention}."), ephemeral=True
        )

    @badge_group.command(name="channel", description="Atur channel tempat panel atur badge diposting.")
    @app_commands.describe(channel="Channel panel atur badge")
    @staff_only()
    async def channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await settings_q.set_setting(self.bot.db, "leaderboard_badge_channel_id", str(channel.id))
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Channel panel badge diatur ke {channel.mention}. Pake `/badge panel` buat posting panelnya."
            ),
            ephemeral=True,
        )

    @badge_group.command(name="background", description="Atur gambar background leaderboard.")
    @app_commands.describe(gambar="Gambar background leaderboard -- kosongin buat balik ke gradient default")
    @staff_only()
    async def background(self, interaction: discord.Interaction, gambar: discord.Attachment | None = None) -> None:
        if gambar is None:
            await settings_q.set_setting(self.bot.db, "leaderboard_background_url", "")
            await interaction.response.send_message(
                embed=embeds.success_embed("Background leaderboard dibalikin ke gradient default."), ephemeral=True
            )
            return
        if not gambar.content_type or not gambar.content_type.startswith("image/"):
            await interaction.response.send_message(
                embed=embeds.error_embed("File yang dilampirin harus berupa gambar."), ephemeral=True
            )
            return
        await settings_q.set_setting(self.bot.db, "leaderboard_background_url", gambar.url)
        await interaction.response.send_message(
            embed=embeds.success_embed(
                "Background leaderboard berhasil diatur. Bakal kepake pas leaderboard di-refresh berikutnya."
            ),
            ephemeral=True,
        )

    @badge_group.command(name="panel", description="Posting panel atur badge leaderboard di channel ini.")
    @app_commands.describe(
        title="Judul panel",
        description="Isi teks panel",
        thumbnail="Gambar kecil di samping judul (opsional)",
        banner="Gambar full-width di bawah teks (opsional)",
        emoji_atur="Emoji tombol Atur Badge -- boleh emoji custom server (opsional)",
        emoji_hapus="Emoji tombol Hapus Badge -- boleh emoji custom server (opsional)",
    )
    @staff_only()
    async def panel(
        self,
        interaction: discord.Interaction,
        title: str = "Atur Badge Leaderboard",
        description: str = (
            "Kamu lagi di TOP 3 Top Spenders? Atur badge custom kamu sendiri di sini."
        ),
        thumbnail: discord.Attachment | None = None,
        banner: discord.Attachment | None = None,
        emoji_atur: str | None = None,
        emoji_hapus: str | None = None,
    ) -> None:
        for attachment, label in ((thumbnail, "Thumbnail"), (banner, "Banner")):
            if attachment is not None and (
                not attachment.content_type or not attachment.content_type.startswith("image/")
            ):
                await interaction.response.send_message(
                    embed=embeds.error_embed(f"{label} harus berupa gambar."), ephemeral=True
                )
                return

        try:
            parsed_emoji_atur = _parse_custom_emoji(emoji_atur)
            parsed_emoji_hapus = _parse_custom_emoji(emoji_hapus)
        except ValueError as exc:
            await interaction.response.send_message(embed=embeds.error_embed(str(exc)), ephemeral=True)
            return

        view_kwargs: dict = {"title": title, "description": description}
        if thumbnail is not None:
            view_kwargs["thumbnail_url"] = thumbnail.url
        if banner is not None:
            view_kwargs["banner_url"] = banner.url
        if parsed_emoji_atur is not None:
            view_kwargs["emoji_set"] = parsed_emoji_atur
        if parsed_emoji_hapus is not None:
            view_kwargs["emoji_clear"] = parsed_emoji_hapus

        await interaction.channel.send(view=BadgePanelView(**view_kwargs))
        await interaction.response.send_message(
            embed=embeds.success_embed("Panel badge udah diposting."), ephemeral=True
        )

    @badge_group.command(name="list", description="Liat siapa aja yang udah atur badge custom-nya.")
    @staff_only()
    async def list_badges(self, interaction: discord.Interaction) -> None:
        rows = await lb_q.list_badges(self.bot.db)
        if not rows:
            await interaction.response.send_message(
                embed=embeds.info_embed("Badge Leaderboard", "Belum ada yang atur badge custom."), ephemeral=True
            )
            return
        lines = [
            f"<@{r['user_id']}> -- **{r['text']}** ({r['color_from']} {chr(0x2192)} {r['color_to']})"
            for r in rows
        ]
        await interaction.response.send_message(
            embed=embeds.info_embed("Badge Leaderboard", "\n".join(lines)), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BadgeCog(bot))
