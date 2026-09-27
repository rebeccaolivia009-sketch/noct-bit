"""Command admin & user: /ticket"""

from __future__ import annotations

import re

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import ticket_types as ticket_types_q
from bot.database.queries import tickets as tickets_q
from bot.ui import embeds
from bot.ui.modals import ReasonModal
from bot.ui.views import OpenTicketPanelView, TicketControlView, TicketTypeSelectView
from bot.utils import ticket_actions
from bot.utils.permissions import is_staff, staff_only

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _normalize_slug(raw: str) -> str:
    """Ubah input staff jadi slug aman buat nama channel & kolom `kind` di
    DB -- huruf kecil, spasi/simbol jadi tanda hubung, dipotong 40
    karakter. Dipake JUGA sebagai custom_id kanal, jadi harus konsisten
    tiap kali dipanggil buat slug yang sama persis."""
    cleaned = _SLUG_RE.sub("-", raw.strip().lower()).strip("-")
    return cleaned[:40]


class TicketCog(commands.Cog):
    """Setup panel ticket plus command open/close/reopen, dan pengaturan
    multi-jenis ticket (Customer Service, Konsultasi, dst) lewat
    /ticket type."""

    ticket_group = app_commands.Group(name="ticket", description="Kelola ticket support.", guild_only=True)
    type_group = app_commands.Group(
        name="type", description="Atur jenis-jenis ticket (Customer Service, Konsultasi, dst).",
        parent=ticket_group,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        ticket = await tickets_q.get_ticket_by_channel(self.bot.db, message.channel.id)
        if ticket and ticket["status"] == "open":
            await tickets_q.touch_activity(self.bot.db, message.channel.id)

    # -- Panel & buka/tutup/reopen ticket (kind="support" bawaan, TETEP jalan
    # persis kayak sebelum ada /ticket type -- lihat docstring
    # bot.utils.ticket_actions soal fallback-nya) ------------------------------

    @ticket_group.command(name="panel", description="Posting panel Open Ticket (1 tombol, jenis 'support') di channel ini.")
    @app_commands.describe(
        title="Judul panel",
        description="Isi teks panel",
        image_url="Gambar banner full-width di bawah teks (PNG/JPG/WebP)",
        thumbnail_url="Logo/thumbnail kecil di kanan atas (PNG/JPG/WebP)",
        button_label="Teks yang muncul di tombol",
    )
    @staff_only()
    async def panel(
        self,
        interaction: discord.Interaction,
        title: str = "NOCTRA -- Support",
        description: str = (
            "Butuh bantuan soal order atau ada pertanyaan buat staff? "
            "Klik di bawah buat buka ticket pribadi."
        ),
        image_url: str | None = None,
        thumbnail_url: str | None = None,
        button_label: str = "Buka Ticket",
    ) -> None:
        embed = embeds.base_embed(title, description, image_url=image_url, thumbnail_url=thumbnail_url)
        await interaction.channel.send(embed=embed, view=OpenTicketPanelView(button_label=button_label))
        await interaction.response.send_message(embed=embeds.success_embed("Panel ticket udah diposting."), ephemeral=True)

    @ticket_group.command(
        name="panel_types",
        description="Posting panel dropdown Components V2 buat pilih jenis ticket di channel ini.",
    )
    @app_commands.describe(
        title="Judul panel",
        description="Isi teks panel (sejajar thumbnail kalau diisi)",
        thumbnail_url="Gambar kecil sejajar deskripsi (PNG/JPG/WebP, opsional)",
        banner_url="Banner full-width di bawah dropdown (PNG/JPG/WebP, opsional)",
        footer_text="Teks footer di paling bawah (opsional)",
        footer_icon_url="Ikon kecil di samping teks footer (PNG/JPG/WebP, opsional)",
    )
    @staff_only()
    async def panel_types(
        self,
        interaction: discord.Interaction,
        title: str = "NOCTRA -- Pilih Jenis Ticket",
        description: str = "Pilih jenis ticket yang sesuai kebutuhan kamu lewat dropdown di bawah.",
        thumbnail_url: str | None = None,
        banner_url: str | None = None,
        footer_text: str | None = None,
        footer_icon_url: str | None = None,
    ) -> None:
        types = await ticket_types_q.list_types(self.bot.db, interaction.guild_id, enabled_only=True)
        if not types:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Belum ada jenis ticket yang diatur. Pake `/ticket type add` dulu buat nambahin, "
                    "misal Customer Service atau Konsultasi."
                ),
                ephemeral=True,
            )
            return
        view = TicketTypeSelectView(
            types,
            title=title,
            description=description,
            thumbnail_url=thumbnail_url,
            banner_url=banner_url,
            footer_text=footer_text,
            footer_icon_url=footer_icon_url,
        )
        await interaction.channel.send(view=view)
        await interaction.response.send_message(
            embed=embeds.success_embed("Panel dropdown jenis ticket udah diposting."), ephemeral=True
        )

    @ticket_group.command(name="open", description="Buka ticket support baru (jenis 'support' bawaan).")
    @app_commands.guild_only()
    async def open_ticket(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = await ticket_actions.create_ticket_channel(
            self.bot, interaction.guild, interaction.user, "support"
        )
        await channel.send(
            content=interaction.user.mention,
            embed=embeds.ticket_welcome_embed(),
            view=TicketControlView(),
        )
        await interaction.followup.send(
            embed=embeds.success_embed(f"Ticket kamu udah dibuat: {channel.mention}"), ephemeral=True
        )

    @ticket_group.command(name="close", description="Tutup ticket yang lagi dibuka ini.")
    @app_commands.describe(reason="Alasan penutupan")
    async def close(self, interaction: discord.Interaction, reason: str | None = None) -> None:
        ticket = await tickets_q.get_ticket_by_channel(self.bot.db, interaction.channel.id)
        if not ticket:
            await interaction.response.send_message(embed=embeds.error_embed("Ini bukan channel ticket."), ephemeral=True)
            return
        if not (await is_staff(interaction) or interaction.user.id == ticket["user_id"]):
            await interaction.response.send_message(
                embed=embeds.error_embed("Cuma staff atau pemilik ticket yang bisa nutup ticket ini."), ephemeral=True
            )
            return

        if reason is not None:
            await interaction.response.defer(ephemeral=True)
            await ticket_actions.close_ticket(self.bot, interaction.channel, str(interaction.user), reason)
            await interaction.followup.send(embed=embeds.success_embed("Ticket udah ditutup."), ephemeral=True)
            return

        async def on_reason(inter: discord.Interaction, typed_reason: str) -> None:
            await inter.response.defer(ephemeral=True)
            await ticket_actions.close_ticket(self.bot, inter.channel, str(inter.user), typed_reason or None)
            await inter.followup.send(embed=embeds.success_embed("Ticket udah ditutup."), ephemeral=True)

        await interaction.response.send_modal(ReasonModal("Tutup Ticket", on_reason))

    @ticket_group.command(name="reopen", description="Buka lagi ticket yang lagi dibuka ini.")
    @staff_only()
    async def reopen(self, interaction: discord.Interaction) -> None:
        ticket = await tickets_q.get_ticket_by_channel(self.bot.db, interaction.channel.id)
        if not ticket:
            await interaction.response.send_message(embed=embeds.error_embed("Ini bukan channel ticket."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await ticket_actions.reopen_ticket(self.bot, interaction.channel, str(interaction.user))
        await interaction.followup.send(embed=embeds.success_embed("Ticket udah dibuka lagi."), ephemeral=True)

    # -- /ticket type: atur jenis-jenis ticket ---------------------------------

    @type_group.command(name="add", description="Tambah/update jenis ticket (misal Customer Service, Konsultasi).")
    @app_commands.describe(
        slug="Kode unik jenis ini (dipake buat nama channel), misal 'cs' atau 'konsultasi' -- huruf/angka aja",
        label="Nama yang muncul di dropdown & disebut ke customer, misal 'Customer Service'",
        kategori="Kategori Discord tempat channel ticket jenis ini dibuka",
        log_channel="Channel transcript pas ticket jenis ini ditutup (opsional -- kosongin buat pake log global)",
        kategori_arsip="Kategori Discord tempat channel dipindah pas ditutup (opsional -- kosongin buat pake arsip global)",
        deskripsi="Keterangan singkat di bawah label pas dropdown dibuka (opsional)",
        emoji="Emoji buat opsi ini di dropdown -- boleh custom dari server lain (opsional)",
    )
    @staff_only()
    async def type_add(
        self,
        interaction: discord.Interaction,
        slug: str,
        label: str,
        kategori: discord.CategoryChannel,
        log_channel: discord.TextChannel | None = None,
        kategori_arsip: discord.CategoryChannel | None = None,
        deskripsi: str | None = None,
        emoji: str | None = None,
    ) -> None:
        normalized_slug = _normalize_slug(slug)
        if not normalized_slug:
            await interaction.response.send_message(
                embed=embeds.error_embed("Slug gak valid -- pake huruf/angka, misal `cs` atau `konsultasi`."),
                ephemeral=True,
            )
            return

        emoji_value: str | None = None
        if emoji:
            try:
                emoji_value = str(discord.PartialEmoji.from_str(emoji.strip()))
            except Exception:  # noqa: BLE001
                await interaction.response.send_message(
                    embed=embeds.error_embed("Emoji gak valid. Pake emoji custom server atau emoji unicode biasa."),
                    ephemeral=True,
                )
                return

        await ticket_types_q.upsert_type(
            self.bot.db,
            guild_id=interaction.guild_id,
            slug=normalized_slug,
            label=label,
            description=deskripsi,
            emoji=emoji_value,
            category_id=kategori.id,
            archive_category_id=kategori_arsip.id if kategori_arsip else None,
            log_channel_id=log_channel.id if log_channel else None,
        )
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Jenis ticket **{label}** (`{normalized_slug}`) berhasil disimpen. "
                f"Posting/update panel dropdown-nya pake `/ticket panel_types`."
            ),
            ephemeral=True,
        )

    @type_group.command(name="remove", description="Hapus jenis ticket.")
    @app_commands.describe(slug="Kode jenis ticket yang mau dihapus (lihat `/ticket type list`)")
    @staff_only()
    async def type_remove(self, interaction: discord.Interaction, slug: str) -> None:
        normalized_slug = _normalize_slug(slug)
        deleted = await ticket_types_q.delete_type(self.bot.db, interaction.guild_id, normalized_slug)
        if not deleted:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Jenis ticket `{normalized_slug}` gak ketemu."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Jenis ticket `{normalized_slug}` dihapus. Panel dropdown yang UDAH keposting sebelumnya "
                "masih nampilin opsi lama sampe di-posting ulang lewat `/ticket panel_types`."
            ),
            ephemeral=True,
        )

    @type_group.command(name="list", description="Liat semua jenis ticket yang udah diatur di server ini.")
    @staff_only()
    async def type_list(self, interaction: discord.Interaction) -> None:
        types = await ticket_types_q.list_types(self.bot.db, interaction.guild_id)
        if not types:
            await interaction.response.send_message(
                embed=embeds.info_embed(
                    "Jenis Ticket", "Belum ada jenis ticket yang diatur. Pake `/ticket type add` buat nambahin."
                ),
                ephemeral=True,
            )
            return

        lines = []
        for t in types:
            category_display = f"<#{t['category_id']}>" if t["category_id"] else "kategori global"
            log_display = f"<#{t['log_channel_id']}>" if t["log_channel_id"] else "log global"
            state = "" if t["enabled"] else " *(nonaktif)*"
            emoji_prefix = f"{t['emoji']} " if t["emoji"] else ""
            lines.append(
                f"{emoji_prefix}**{t['label']}** (`{t['slug']}`){state}\n"
                f"{chr(0x2022)} Kategori: {category_display}  {chr(0x2022)} Log: {log_display}"
            )
        await interaction.response.send_message(
            embed=embeds.info_embed("Jenis Ticket", "\n\n".join(lines)), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TicketCog(bot))
