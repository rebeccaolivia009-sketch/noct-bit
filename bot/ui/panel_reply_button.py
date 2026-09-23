"""
Tombol "Reply" yang ditambahin lewat /panel atau /announcement -- beda
sama tombol link (ditangani Discord doang di sisi client, bot gak pernah
dapet interaction-nya), tombol ini beneran ngirim balesan pesan pas
diklik. ID row `panel_reply_buttons` di-encode di custom_id-nya sendiri,
sama kayak trik OrderActionButton/ReviewStartButton -- jadi tetep jalan
abis bot restart tanpa bookkeeping ekstra.
"""

from __future__ import annotations

import discord

from bot.database.queries import panel_buttons as panel_buttons_q
from bot.ui import embeds

PANEL_REPLY_CUSTOM_ID_PREFIX = "noctra:panelbtn:"


class PanelReplyButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"noctra:panelbtn:(?P<button_id>[0-9]+)",
):
    def __init__(self, button_id: int, *, label: str = "...", emoji: str | None = None) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                emoji=emoji,
                style=discord.ButtonStyle.secondary,
                custom_id=f"{PANEL_REPLY_CUSTOM_ID_PREFIX}{button_id}",
            )
        )
        self.button_id = button_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):  # noqa: D102
        return cls(int(match["button_id"]), label=item.label or "...", emoji=item.emoji)

    async def callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        row = await panel_buttons_q.get_reply_button(db, self.button_id)
        if not row:
            await interaction.response.send_message(
                embed=embeds.error_embed("Tombol ini udah gak valid -- mungkin data lamanya kehapus."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=embeds.info_embed(row["label"], row["reply_text"]), ephemeral=True
        )
