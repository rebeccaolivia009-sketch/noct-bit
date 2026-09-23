"""
View builder buat command /panel -- edit pesan Components V2 custom di
channel yang sama, live lewat panel kontrol ephemeral. Draft-nya
dipersist ke tabel panel_drafts tiap ada perubahan, biar bisa DILANJUTIN
EDIT KAPAN AJA lewat /panel message:<link/ID> -- gak cuma sekali sesi
doang selagi view ini masih nyangkut di memori (dulu, sebelum ini ada,
begitu sesi/pesan ephemeral-nya ilang, pesan yang udah keposting gak
bisa diapa-apain lagi).
"""

from __future__ import annotations

import json

import discord

from bot.database.queries import panel_drafts as panel_drafts_q
from bot.ui import embeds
from bot.ui.draft_builder_base import BaseDraftBuilderView
from bot.utils.message_draft import MessageDraft, draft_to_dict, render_draft_layout


class PanelBuilderView(BaseDraftBuilderView):
    """Semua tombol edit draft (Title/Description/dst) diwarisin dari
    BaseDraftBuilderView -- di sini nambahin tombol Update, plus override
    `_after_edit()` biar SETIAP perubahan langsung kepush live ke pesan
    target (`target_message_id`) di channel yang sama tempat /panel
    dijalanin -- gak perlu nunggu klik Update dulu buat liat hasilnya."""

    def __init__(
        self, target_channel_id: int, target_message_id: int, draft: MessageDraft | None = None
    ) -> None:
        super().__init__(timeout=1800)
        self.target_channel_id = target_channel_id
        self.target_message_id = target_message_id
        if draft is not None:
            # Draft dimuat dari tabel panel_drafts (lagi lanjutin edit
            # pesan lama) -- ganti draft kosong bawaan BaseDraftBuilderView,
            # terus bangun ulang opsi separator select-nya biar konsisten
            # sama isi draft yang baru dimuat.
            self.draft = draft
            self._build_separator_select()

    async def _persist(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        await panel_drafts_q.save_draft(
            db, self.target_message_id, self.target_channel_id, json.dumps(draft_to_dict(self.draft))
        )

    async def _after_edit(self, interaction: discord.Interaction) -> None:
        # Response PERTAMA interaction ini WAJIB edit_message -- ini yang
        # ngerefresh pesan panel sendiri (opsi Select dsb).
        await interaction.response.edit_message(view=self)

        await self._persist(interaction)

        # Push live ke pesan TARGET asli -- ini pesan biasa (bukan
        # ephemeral), jadi aman di-edit lewat channel.fetch_message() +
        # .edit() biasa pake kredensial bot, gak perlu lewat mekanisme
        # response interaction sama sekali.
        channel = interaction.client.get_channel(self.target_channel_id)  # type: ignore[attr-defined]
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            target_message = await channel.fetch_message(self.target_message_id)
            await target_message.edit(view=render_draft_layout(self.draft))
        except (discord.NotFound, discord.HTTPException):
            pass  # pesan target kehapus atau lagi ada masalah -- gak fatal, draft-nya tetep udah kesimpen di atas

    @discord.ui.button(label="Update", style=discord.ButtonStyle.success, row=4)
    async def update_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Isinya udah live ke-push tiap ada perubahan (lihat _after_edit di
        # atas) -- tombol ini tetep ada buat konfirmasi eksplisit "beres"
        # dan sekalian re-sync manual kalau-kalau ada push yang gagal
        # sebelumnya (misal pesan target sempet gak ketemu).
        await interaction.response.defer(ephemeral=True)
        await self._persist(interaction)

        channel = interaction.client.get_channel(self.target_channel_id)  # type: ignore[attr-defined]
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(embed=embeds.error_embed("Channel target gak ketemu."), ephemeral=True)
            return
        try:
            target_message = await channel.fetch_message(self.target_message_id)
        except discord.NotFound:
            await interaction.followup.send(
                embed=embeds.error_embed(
                    "Pesan target udah kehapus -- draft kamu tetep kesimpen, tapi gak ada pesan buat "
                    "di-update lagi. Jalanin `/panel` lagi buat mulai pesan baru."
                ),
                ephemeral=True,
            )
            return

        try:
            await target_message.edit(view=render_draft_layout(self.draft))
        except discord.HTTPException as exc:
            await interaction.followup.send(embed=embeds.error_embed(f"Gagal update pesan: {exc}"), ephemeral=True)
            return

        await interaction.followup.send(
            embed=embeds.success_embed(
                f"Berhasil update pesan di {channel.mention}. Draft ini kesimpen -- bisa dilanjutin edit "
                f"kapan aja lewat `/panel message:{target_message.jump_url}`."
            ),
            ephemeral=True,
        )
