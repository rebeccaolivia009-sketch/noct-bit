"""
Panel setting Auto Reaction (/autoreact set): preview di atas, kontrol di
bawah. Semua perubahan lewat draft di memori; database baru disentuh pas
Simpan. Pola yang sama dengan panel Sticky (bot.ui.sticky).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord

from bot.core.logger import logger
from bot.core.theme import COLOR_ACCENT, COLOR_DANGER, COLOR_PRIMARY
from bot.database.queries import auto_reactions as auto_reactions_q
from bot.ui import embeds
from bot.ui.builder import Btn, FormModal
from bot.utils import autoreact_spec as spec

_STYLE = discord.ButtonStyle
OWNER_ONLY = "Panel ini punya staff lain -- jalanin `/autoreact set` buat buka panelmu sendiri."


def channel_problems(bot, channel, emojis: list[str]) -> list[str]:
    """Kenapa bot gak bisa nambah reaksi di `channel` (kosong = aman)."""
    if channel is None:
        return ["Channel-nya gak ketemu (mungkin udah dihapus)."]
    perms = channel.permissions_for(channel.guild.me)
    problems = []
    if not perms.view_channel:
        problems.append("Bot gak bisa lihat channel itu (izin View Channel).")
    if not perms.read_message_history:
        problems.append("Bot butuh izin Read Message History (wajib buat nambah reaksi).")
    if not perms.add_reactions:
        problems.append("Bot gak punya izin Add Reactions di channel itu.")
    for emoji in emojis:
        emoji_id = spec.custom_emoji_id(emoji)
        if emoji_id is None:
            continue
        found = bot.get_emoji(emoji_id)
        if found is None:
            problems.append(f"Emoji custom {emoji} gak bisa diakses bot (bot harus ada di server pemilik emoji itu).")
        elif found.guild_id != channel.guild.id and not perms.use_external_emojis:
            problems.append(f"Emoji {emoji} dari server lain -- bot butuh izin Use External Emojis di channel ini.")
    return problems


async def _say(interaction: discord.Interaction, text: str) -> None:
    try:
        embed = embeds.error_embed(text)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass


class AutoReactPanelView(discord.ui.LayoutView):
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
        logger.exception("Error di panel Auto Reaction.", exc_info=error)
        await _say(interaction, "Ada yang error di panel. Coba lagi, atau buka ulang `/autoreact set`.")


class AutoReactPanel:
    def __init__(self, owner_id: int, channel, row) -> None:
        self.owner_id = owner_id
        self.channel_id = channel.id
        self.channel_name = getattr(channel, "name", str(channel.id))
        self.guild_id = channel.guild.id
        self.existing = row is not None
        self.original = spec.values_from_row(row, auto_reactions_q.emojis_of(row)) if row else None
        self.values = (
            {**self.original, "emojis": list(self.original["emojis"])} if row else spec.new_values()
        )
        self.confirm_delete = False
        self.notice: str | None = None

    def btn(self, handler, *, label=None, emoji=None, style=_STYLE.secondary, disabled=False) -> Btn:
        return Btn(self._guard(handler), label=label, emoji=emoji, style=style, disabled=disabled)

    def _guard(self, handler: Callable[[discord.Interaction], Awaitable[None]]):
        async def run(interaction: discord.Interaction) -> None:
            try:
                await handler(interaction)
            except Exception:  # noqa: BLE001
                logger.exception("Error di handler panel Auto Reaction.")
                await _say(interaction, "Ada yang error. Coba lagi, atau buka ulang `/autoreact set`.")
        return run

    # -- render ----------------------------------------------------------------

    def _preview(self) -> discord.ui.Container:
        v = self.values
        reactions = " ".join(v["emojis"]) if v["emojis"] else "*(belum ada emoji)*"
        lines = [
            f"## \u26A1 Auto Reaction \u2014 #{self.channel_name}",
            "-# Pratinjau",
            "> \U0001F4AC *contoh pesan di channel ini*",
            reactions,
            "",
            f"**Pesan yang kena:** {spec.MODE_LABEL[v['filter_mode']]}",
            f"**Abaikan bot:** {'ya' if v['ignore_bots'] else 'tidak'}",
        ]
        return discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(
                f"-# Bot nambah reaksi di atas ke tiap pesan baru yang cocok, urut dari kiri ke kanan (maks {spec.MAX_EMOJIS})."
            ),
            accent_colour=COLOR_ACCENT,
        )

    async def render(self, bot) -> AutoReactPanelView:
        notice, self.notice = self.notice, None
        channel = bot.get_channel(self.channel_id)
        v = self.values
        problems = spec.validate(v) + channel_problems(bot, channel, v["emojis"])
        changes = spec.diff(self.original, v)
        enabled = bool(v["enabled"])

        origin = "\u270F\uFE0F Sudah tersimpan" if self.existing else "\u2795 Baru -- belum disimpan"
        lines = [f"{origin} \u00B7 Status: **{'aktif' if enabled else 'nonaktif'}**"]
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
            controls.append(discord.ui.TextDisplay("\u2705 **Siap disimpan.** Cek pratinjau di atas."))

        controls.append(discord.ui.ActionRow(
            self.btn(self._edit_emojis, label="Emoji", emoji="\U0001F600", style=_STYLE.primary),
            self.btn(self._cycle_mode, label=f"Filter: {spec.MODE_SHORT[v['filter_mode']]}", emoji="\U0001F3AF"),
            self.btn(self._flip_bots, label=f"Abaikan bot: {'ya' if v['ignore_bots'] else 'tidak'}", emoji="\U0001F916"),
            self.btn(self._toggle, label=f"Status: {'aktif' if enabled else 'nonaktif'}"),
        ))
        no_change = self.existing and not changes
        if self.confirm_delete:
            controls.append(discord.ui.TextDisplay(
                "\u26A0\uFE0F **Hapus auto reaction ini?** Pengaturannya dihapus permanen. Reaksi yang udah terpasang di pesan lama tetap ada."
            ))
            controls.append(discord.ui.ActionRow(
                self.btn(self._confirm_delete_yes, label="Ya, hapus", emoji="\U0001F5D1\uFE0F", style=_STYLE.danger),
                self.btn(self._confirm_delete_no, label="Batal", emoji="\u2716\uFE0F"),
            ))
        else:
            row = [self.btn(self._save, label="Simpan", emoji="\u2705", style=_STYLE.success,
                            disabled=bool(problems) or no_change)]
            if self.existing:
                row.append(self.btn(self._ask_delete, label="Hapus", emoji="\U0001F5D1\uFE0F", style=_STYLE.danger))
            row.append(self.btn(self._close, label="Tutup", emoji="\u2716\uFE0F"))
            controls.append(discord.ui.ActionRow(*row))
        return AutoReactPanelView(self.owner_id, [
            self._preview(),
            discord.ui.Container(*controls, accent_colour=COLOR_DANGER if problems else COLOR_PRIMARY),
        ])

    async def refresh(self, interaction: discord.Interaction, notice: str | None = None) -> None:
        if notice is not None:
            self.notice = notice
        view = await self.render(interaction.client)
        if interaction.response.is_done():
            await interaction.edit_original_response(view=view)
        else:
            await interaction.response.edit_message(view=view)

    # -- aksi ------------------------------------------------------------------

    async def _edit_emojis(self, interaction: discord.Interaction) -> None:
        async def submit(inter: discord.Interaction, raw: dict) -> None:
            updates, errors = spec.apply_form(raw)
            self.values.update(updates)
            await self.refresh(inter, ("\u26A0\uFE0F " + " ".join(errors)) if errors else None)

        defaults = {"emojis": " ".join(self.values["emojis"])}
        await interaction.response.send_modal(FormModal("Auto Reaction: Emoji", spec.FORMS["emojis"], defaults, submit))

    async def _cycle_mode(self, interaction: discord.Interaction) -> None:
        self.values["filter_mode"] = spec.cycle_mode(self.values["filter_mode"])
        await self.refresh(interaction)

    async def _flip_bots(self, interaction: discord.Interaction) -> None:
        self.values["ignore_bots"] = 0 if self.values["ignore_bots"] else 1
        await self.refresh(interaction)

    async def _toggle(self, interaction: discord.Interaction) -> None:
        self.values["enabled"] = 0 if self.values["enabled"] else 1
        await self.refresh(interaction)

    async def _save(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        v = self.values
        problems = spec.validate(v) + channel_problems(bot, bot.get_channel(self.channel_id), v["emojis"])
        if problems:
            await self.refresh(interaction, "\u26A0\uFE0F Belum bisa disimpan: " + problems[0])
            return
        await auto_reactions_q.upsert(
            bot.db, self.guild_id, self.channel_id, emojis=list(v["emojis"]), filter_mode=v["filter_mode"],
            ignore_bots=bool(v["ignore_bots"]), enabled=bool(v["enabled"]), created_by=self.owner_id,
        )
        self.original = {**v, "emojis": list(v["emojis"])}
        self.existing = True
        cog = bot.get_cog("AutoReactCog")
        if cog is None:
            await self.refresh(interaction, "\u26A0\uFE0F Tersimpan, tapi fitur auto reaction belum aktif di bot (cog gak ke-load).")
            return
        await cog.apply(self.channel_id)
        if v["enabled"]:
            await self.refresh(interaction, f"\u2705 Tersimpan. Pesan baru di <#{self.channel_id}> bakal dikasih reaksi {' '.join(v['emojis'])}.")
        else:
            await self.refresh(interaction, "\u2705 Tersimpan, tapi auto reaction nonaktif (gak ada reaksi yang ditambahin).")

    async def _ask_delete(self, interaction: discord.Interaction) -> None:
        self.confirm_delete = True
        await self.refresh(interaction)

    async def _confirm_delete_no(self, interaction: discord.Interaction) -> None:
        self.confirm_delete = False
        await self.refresh(interaction, "Dibatalin -- auto reaction tetap ada.")

    async def _confirm_delete_yes(self, interaction: discord.Interaction) -> None:
        cog = interaction.client.get_cog("AutoReactCog")
        if cog is not None:
            await cog.remove(self.channel_id)
        else:
            await auto_reactions_q.delete(interaction.client.db, self.channel_id)
        closed = discord.ui.LayoutView(timeout=None)
        closed.add_item(discord.ui.Container(
            discord.ui.TextDisplay(f"## \u26A1 Auto Reaction\n\U0001F5D1\uFE0F Auto reaction di <#{self.channel_id}> dihapus."),
            accent_colour=COLOR_PRIMARY,
        ))
        await interaction.response.edit_message(view=closed)

    async def _close(self, interaction: discord.Interaction) -> None:
        closed = discord.ui.LayoutView(timeout=None)
        closed.add_item(discord.ui.Container(
            discord.ui.TextDisplay("## \u26A1 Auto Reaction\nPanel ditutup. Jalanin `/autoreact set` buat buka lagi."),
            accent_colour=COLOR_PRIMARY,
        ))
        await interaction.response.edit_message(view=closed)
