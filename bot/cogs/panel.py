"""Command admin: /panel -- panel builder buat bikin/edit pesan custom Components V2."""

from __future__ import annotations

import json
import re

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import panel_drafts as panel_drafts_q
from bot.ui import embeds
from bot.ui.panel_builder import PanelBuilderView
from bot.utils.message_draft import MessageDraft, draft_from_dict, render_draft_layout
from bot.utils.permissions import staff_only

# Nangkep link pesan Discord (https://discord.com/channels/GUILD/CHANNEL/MESSAGE)
# -- dipake biar parameter `message` bisa nerima link asli (hasil klik kanan
# "Copy Message Link"), bukan cuma ID mentah.
_MESSAGE_LINK_RE = re.compile(r"channels/\d+/(?P<channel_id>\d+)/(?P<message_id>\d+)")


def _parse_message_reference(value: str, fallback_channel_id: int) -> tuple[int, int] | None:
    """Terima link pesan penuh ATAU ID mentah (dianggep pesan itu ada di
    channel tempat /panel dijalanin). Return (channel_id, message_id) atau
    None kalau formatnya gak dikenalin sama sekali."""
    value = value.strip()
    match = _MESSAGE_LINK_RE.search(value)
    if match:
        return int(match["channel_id"]), int(match["message_id"])
    if value.isdigit():
        return fallback_channel_id, int(value)
    return None


class PanelCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="panel",
        description="Buka panel builder -- bikin pesan baru, atau lanjutin edit pesan panel yang udah ada.",
    )
    @app_commands.describe(
        message="Link atau ID pesan panel yang mau dilanjutin edit -- kosongin buat bikin pesan baru"
    )
    @app_commands.guild_only()
    @staff_only()
    async def panel(self, interaction: discord.Interaction, message: str | None = None) -> None:
        if message:
            await self._resume_existing(interaction, message)
            return

        layout = render_draft_layout(MessageDraft())
        target_message = await interaction.channel.send(view=layout)
        # Simpen draft kosongnya dari awal -- biar /panel message:<link> ke
        # pesan ini SELALU nemuin sesuatu buat dimuat, gak peduli staff
        # sempet ngedit apapun apa enggak sebelum sesi ephemeral-nya ilang.
        await panel_drafts_q.save_draft(
            self.bot.db, target_message.id, interaction.channel.id, json.dumps({})
        )

        panel_view = PanelBuilderView(target_channel_id=interaction.channel.id, target_message_id=target_message.id)
        await interaction.response.send_message(
            embed=embeds.info_embed(
                "Panel Builder",
                f"Lagi bangun pesan [ini]({target_message.jump_url}). Pake tombol di bawah "
                "buat ngedit, terus klik **Update** abis selesai biar keapply ke pesannya.\n\n"
                f"Bisa dilanjutin edit kapan aja lewat `/panel message:{target_message.jump_url}`.",
            ),
            view=panel_view,
            ephemeral=True,
        )

    async def _resume_existing(self, interaction: discord.Interaction, message_ref: str) -> None:
        parsed = _parse_message_reference(message_ref, interaction.channel.id)
        if not parsed:
            await interaction.response.send_message(
                embed=embeds.error_embed("Format link/ID pesan gak dikenalin. Paste link pesan aslinya, atau ID-nya doang."),
                ephemeral=True,
            )
            return
        channel_id, message_id = parsed

        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=embeds.error_embed("Channel dari pesan itu gak ketemu (bot mungkin gak akses ke situ)."),
                ephemeral=True,
            )
            return

        try:
            target_message = await channel.fetch_message(message_id)
        except discord.NotFound:
            await interaction.response.send_message(
                embed=embeds.error_embed("Pesan itu gak ketemu -- mungkin udah kehapus."), ephemeral=True
            )
            return

        row = await panel_drafts_q.get_draft(self.bot.db, message_id)
        if not row:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Pesan ini gak punya draft tersimpen -- kemungkinan besar dibuat sebelum fitur "
                    "lanjut-edit ini ada, atau bukan dari `/panel` sama sekali. Gak bisa dilanjutin edit; "
                    "jalanin `/panel` tanpa parameter `message` buat bikin pesan baru."
                ),
                ephemeral=True,
            )
            return

        data = json.loads(row["draft_json"]) if row["draft_json"] else {}
        draft = draft_from_dict(data) if data else MessageDraft()

        panel_view = PanelBuilderView(target_channel_id=channel_id, target_message_id=message_id, draft=draft)
        await interaction.response.send_message(
            embed=embeds.info_embed(
                "Panel Builder",
                f"Lanjutin edit [pesan ini]({target_message.jump_url}) dari draft yang tersimpen. "
                "Pake tombol di bawah buat ngedit, klik **Update** buat konfirmasi.",
            ),
            view=panel_view,
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PanelCog(bot))
