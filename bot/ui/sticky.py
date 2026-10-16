"""
Tampilan Sticky Message + panel setting-nya (/sticky set).

  * build_container() -- SATU-SATUNYA tempat susunan pesan sticky dibangun:
        Judul -> pemisah -> deskripsi -> pemisah -> thumbnail (+ teks bawah)
    Dipakai buat pesan yang dipasang di channel DAN buat preview di panel,
    jadi apa yang kelihatan di preview PERSIS sama dengan yang terpasang.
  * StickyPanel -- panel setting ephemeral: preview di atas, kontrol di
    bawah. Semua perubahan lewat draft di memori; database baru disentuh
    pas Simpan & Pasang.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord

from bot.core.logger import logger
from bot.core.theme import COLOR_ACCENT, COLOR_DANGER, COLOR_PRIMARY
from bot.database.queries import stickies as stickies_q
from bot.ui import components, embeds
from bot.ui.builder import Btn, FormModal
from bot.utils import sticky_spec as spec
from bot.utils.builder_spec import valid_url

_STYLE = discord.ButtonStyle
OWNER_ONLY = "Panel ini punya staff lain -- jalanin `/sticky set` buat buka panelmu sendiri."


# ============================================================================
# PESAN STICKY
# ============================================================================

def build_container(values: dict, *, preview: bool = False) -> discord.ui.Container:
    """Susunan pesan sticky. `preview=True` ngisi placeholder buat bagian
    yang belum diisi (cuma dipake di panel, gak pernah buat pesan asli)."""
    title = (values.get("title") or "").strip()
    description = (values.get("description") or "").strip()
    if preview:
        title = title or "*(judul belum diisi)*"
        description = description or "*(deskripsi belum diisi)*"

    children: list = [
        discord.ui.TextDisplay(f"## {title}"),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay(description),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
    ]

    footer = (values.get("footer_text") or "").strip()
    footer_text = f"-# {footer}" if footer else components._footer_line()
    thumbnail = (values.get("thumbnail_url") or "").strip()
    if thumbnail and valid_url(thumbnail):
        children.append(
            discord.ui.Section(
                discord.ui.TextDisplay(footer_text),
                accessory=discord.ui.Thumbnail(media=thumbnail),
            )
        )
    else:
        children.append(discord.ui.TextDisplay(footer_text))

    color = values.get("accent_color")
    return discord.ui.Container(*children, accent_colour=color if color is not None else COLOR_ACCENT)


def build_view(values: dict) -> discord.ui.LayoutView:
    """View statis buat dikirim ke channel (gak ada komponen interaktif)."""
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(build_container(values))
    return view


def channel_problems(channel) -> list[str]:
    """Kenapa bot gak bisa masang sticky di `channel` (kosong = aman)."""
    if channel is None:
        return ["Channel-nya gak ketemu (mungkin udah dihapus)."]
    perms = channel.permissions_for(channel.guild.me)
    problems = []
    if not perms.view_channel:
        problems.append("Bot gak bisa lihat channel itu (izin View Channel).")
    if not perms.send_messages:
        problems.append("Bot gak punya izin Send Messages di channel itu.")
    return problems


# ============================================================================
# PANEL SETTING
# ============================================================================

async def _say(interaction: discord.Interaction, text: str) -> None:
    try:
        embed = embeds.error_embed(text)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass


class StickyPanelView(discord.ui.LayoutView):
    def __init__(self, owner_id: int, items: list) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        for item in items:
            self.add_item(item)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await _say(interaction, OWNER_ONLY)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        logger.exception("Error di panel Sticky.", exc_info=error)
        await _say(interaction, "Ada yang error di panel. Coba lagi, atau buka ulang `/sticky set`.")


class StickyPanel:
    """Satu instance per panel yang dibuka: nyimpen draft + state, lalu
    render ulang view tiap ada interaksi."""

    def __init__(self, owner_id: int, channel, row) -> None:
        self.owner_id = owner_id
        self.channel_id = channel.id
        self.channel_name = getattr(channel, "name", str(channel.id))
        self.guild_id = channel.guild.id
        self.existing = row is not None
        self.posted = bool(row and row["message_id"])
        self.original = spec.values_from_row(row) if row else None
        self.values = dict(self.original) if row else spec.new_values()
        self.confirm_delete = False
        self.notice: str | None = None

    # -- util ------------------------------------------------------------------

    def btn(self, handler, *, label=None, emoji=None, style=_STYLE.secondary, disabled=False) -> Btn:
        return Btn(self._guard(handler), label=label, emoji=emoji, style=style, disabled=disabled)

    def _guard(self, handler: Callable[[discord.Interaction], Awaitable[None]]):
        async def run(interaction: discord.Interaction) -> None:
            try:
                await handler(interaction)
            except Exception:  # noqa: BLE001
                logger.exception("Error di handler panel Sticky.")
                await _say(interaction, "Ada yang error. Coba lagi, atau buka ulang `/sticky set`.")
        return run

    # -- render ----------------------------------------------------------------

    async def render(self, bot) -> StickyPanelView:
        notice, self.notice = self.notice, None
        channel = bot.get_channel(self.channel_id)
        problems = spec.validate(self.values) + channel_problems(channel)
        changes = spec.diff(self.original, self.values)

        if not self.existing:
            state = "\u2795 Sticky baru -- belum dipasang"
        elif self.posted:
            state = "\U0001F4CC Terpasang"
        else:
            state = "\u23F8\uFE0F Tersimpan, tapi gak terpasang"
        enabled = bool(self.values.get("enabled"))

        lines = [
            f"## \U0001F4CC Sticky Message \u2014 #{self.channel_name}",
            f"{state} \u00B7 Status: **{'aktif' if enabled else 'nonaktif'}**",
            "-# Tiap ada pesan baru di channel, sticky dipindah lagi ke paling bawah "
            "(ada jeda beberapa detik biar gak kena rate limit).",
        ]
        if notice:
            lines.append(f"> {notice}")
        controls: list = [discord.ui.TextDisplay("\n".join(lines)), discord.ui.Separator(visible=True)]

        if problems:
            controls.append(discord.ui.TextDisplay(
                "\u26A0\uFE0F **Belum bisa disimpan:**\n" + "\n".join(f"\u2022 {p}" for p in problems)
            ))
        elif self.existing and changes:
            controls.append(discord.ui.TextDisplay(
                f"**Perubahan ({len(changes)})** \u2014 belum disimpan\n" + "\n".join(changes)
            ))
        elif self.existing:
            controls.append(discord.ui.TextDisplay("*Belum ada perubahan dari yang tersimpan.*"))
        else:
            controls.append(discord.ui.TextDisplay("\u2705 **Siap dipasang.** Cek preview di atas."))

        controls.append(discord.ui.ActionRow(
            self.btn(self._form("text"), label="Judul & Teks", emoji="\u270F\uFE0F", style=_STYLE.primary),
            self.btn(self._form("look"), label="Tampilan", emoji="\U0001F3A8", style=_STYLE.primary),
            self.btn(self._toggle, label=f"Status: {'aktif' if enabled else 'nonaktif'}"),
        ))

        no_change = self.existing and not changes
        if self.confirm_delete:
            controls.append(discord.ui.TextDisplay(
                "\u26A0\uFE0F **Hapus sticky ini?** Pesannya dicopot dari channel dan pengaturannya dihapus permanen."
            ))
            controls.append(discord.ui.ActionRow(
                self.btn(self._confirm_delete_yes, label="Ya, hapus", emoji="\U0001F5D1\uFE0F", style=_STYLE.danger),
                self.btn(self._confirm_delete_no, label="Batal", emoji="\u2716\uFE0F"),
            ))
        else:
            row = [
                self.btn(self._save, label="Simpan & Pasang", emoji="\u2705", style=_STYLE.success,
                         disabled=bool(problems) or no_change),
            ]
            if self.existing:
                row.append(self.btn(self._ask_delete, label="Hapus", emoji="\U0001F5D1\uFE0F", style=_STYLE.danger))
            row.append(self.btn(self._close, label="Tutup", emoji="\u2716\uFE0F"))
            controls.append(discord.ui.ActionRow(*row))

        accent = COLOR_DANGER if problems else COLOR_PRIMARY
        return StickyPanelView(
            self.owner_id,
            [
                build_container(self.values, preview=True),
                discord.ui.Container(*controls, accent_colour=accent),
            ],
        )

    async def refresh(self, interaction: discord.Interaction, notice: str | None = None) -> None:
        if notice is not None:
            self.notice = notice
        view = await self.render(interaction.client)
        if interaction.response.is_done():
            await interaction.edit_original_response(view=view)
        else:
            await interaction.response.edit_message(view=view)

    # -- aksi ------------------------------------------------------------------

    def _form(self, form: str):
        async def handler(interaction: discord.Interaction) -> None:
            async def submit(inter: discord.Interaction, raw: dict) -> None:
                updates, errors = spec.apply_form(form, raw)
                self.values.update(updates)
                await self.refresh(inter, ("\u26A0\uFE0F " + " ".join(errors)) if errors else None)

            await interaction.response.send_modal(
                FormModal(spec.FORM_TITLE[form], spec.FORMS[form], spec.form_defaults(form, self.values), submit)
            )
        return handler

    async def _toggle(self, interaction: discord.Interaction) -> None:
        self.values["enabled"] = 0 if self.values.get("enabled") else 1
        await self.refresh(interaction)

    async def _save(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        problems = spec.validate(self.values) + channel_problems(bot.get_channel(self.channel_id))
        if problems:
            await self.refresh(interaction, "\u26A0\uFE0F Belum bisa disimpan: " + problems[0])
            return
        v = self.values
        clean = {
            "title": v["title"].strip(),
            "description": v["description"].strip(),
            "footer_text": (v.get("footer_text") or "").strip() or None,
            "thumbnail_url": (v.get("thumbnail_url") or "").strip() or None,
            "accent_color": v.get("accent_color"),
            "enabled": int(bool(v.get("enabled"))),
        }
        await stickies_q.upsert(
            bot.db, self.guild_id, self.channel_id,
            title=clean["title"], description=clean["description"], footer_text=clean["footer_text"],
            thumbnail_url=clean["thumbnail_url"], accent_color=clean["accent_color"],
            enabled=bool(clean["enabled"]), created_by=self.owner_id,
        )
        self.values = dict(clean)
        self.original = dict(clean)
        self.existing = True

        cog = bot.get_cog("StickyCog")
        if cog is None:
            await self.refresh(interaction, "\u26A0\uFE0F Tersimpan, tapi fitur sticky belum aktif di bot (cog gak ke-load).")
            return
        ok, error = await cog.apply(self.channel_id)
        row = await stickies_q.get_by_channel(bot.db, self.channel_id)
        self.posted = bool(row and row["message_id"])
        if not ok:
            await self.refresh(interaction, f"\u26A0\uFE0F Tersimpan, tapi gagal dipasang: {error}")
        elif clean["enabled"]:
            await self.refresh(interaction, f"\u2705 Dipasang di <#{self.channel_id}>.")
        else:
            await self.refresh(interaction, "\u2705 Tersimpan. Sticky nonaktif, pesannya dicopot dari channel.")

    async def _ask_delete(self, interaction: discord.Interaction) -> None:
        self.confirm_delete = True
        await self.refresh(interaction)

    async def _confirm_delete_no(self, interaction: discord.Interaction) -> None:
        self.confirm_delete = False
        await self.refresh(interaction, "Dibatalin -- sticky tetap ada.")

    async def _confirm_delete_yes(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("StickyCog")
        if cog is not None:
            await cog.remove(self.channel_id)
        else:
            await stickies_q.delete(interaction.client.db, self.channel_id)
        closed = discord.ui.LayoutView(timeout=None)
        closed.add_item(discord.ui.Container(
            discord.ui.TextDisplay(f"## \U0001F4CC Sticky Message\n\U0001F5D1\uFE0F Sticky di <#{self.channel_id}> dihapus."),
            accent_colour=COLOR_PRIMARY,
        ))
        await interaction.response.edit_message(view=closed)

    async def _close(self, interaction: discord.Interaction) -> None:
        closed = discord.ui.LayoutView(timeout=None)
        closed.add_item(discord.ui.Container(
            discord.ui.TextDisplay("## \U0001F4CC Sticky Message\nPanel ditutup. Jalanin `/sticky set` buat buka lagi."),
            accent_colour=COLOR_PRIMARY,
        ))
        await interaction.response.edit_message(view=closed)
