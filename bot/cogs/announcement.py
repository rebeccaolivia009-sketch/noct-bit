"""Command admin: /announcement -- bangun pengumuman lewat panel builder, kirim ke channel pilihan."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.ui.announcement_builder import AnnouncementBuilderView
from bot.utils.message_draft import render_draft_preview_embed
from bot.utils.permissions import staff_only


class AnnouncementCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="announcement", description="Buka panel builder buat bikin pengumuman, kirim ke channel pilihan.")
    @app_commands.describe(channel="Channel tujuan buat pengumuman ini")
    @app_commands.guild_only()
    @staff_only()
    async def announcement(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        builder_view = AnnouncementBuilderView(target_channel_id=channel.id)
        preview = render_draft_preview_embed(builder_view.draft)
        preview.set_author(name=f"Preview -- bakal dikirim ke #{channel.name}")
        await interaction.response.send_message(embed=preview, view=builder_view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnnouncementCog(bot))
