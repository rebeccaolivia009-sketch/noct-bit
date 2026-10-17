"""
Kartu Statistik Pembelian (/stats) dan riwayat pesanan -- Components V2.

Susunan kartu:
    Judul (+ label & ornamen sesuai level)
    ---- pemisah ----
    Nama, peringkat, total pesanan, total spend      [ avatar ]
    ---- pemisah ----           (kebuka sesuai level)
    Info eksklusif + progress menuju tier berikutnya
    Kalimat penutup                          [ Riwayat Pesanan ]

Gayanya (warna, ornamen, info eksklusif) ngikutin total spend dan
peringkat -- aturan lengkap di bot.utils.stats_style.

Tombol-tombolnya DynamicItem (custom_id bawa id pemilik kartu), jadi tetep
hidup setelah bot restart, termasuk di kartu yang diposting publik.
"""

from __future__ import annotations

import time

import discord

from bot.core.logger import logger
from bot.core.theme import COLOR_ACCENT
from bot.ui import components, embeds
from bot.utils import stats_data
from bot.utils import stats_style as style

_STYLE = discord.ButtonStyle
HISTORY_COOLDOWN = 8.0   # detik antar pengiriman riwayat per user (biar channel gak dibanjiri)
_last_history: dict[int, float] = {}


async def _say(interaction: discord.Interaction, text: str) -> None:
    try:
        embed = embeds.error_embed(text)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass


# ============================================================================
# KARTU
# ============================================================================

def build_card_container(d: style.CardData, *, history_button: bool = True) -> discord.ui.Container:
    st = style.style_for(d.level, d.crown)
    header = f"## {st.emoji} {st.title}\n-# {st.label}"
    if st.frame:
        header += f"\n-# {st.frame}"

    children: list = [
        discord.ui.TextDisplay(header),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
    ]

    if d.excluded:
        stats_text = f"**{d.name}**\nAkun ini dikecualiin dari leaderboard, jadi gak punya statistik pembelian."
    else:
        stats_text = "\n".join(style.core_lines(d))
    if d.avatar_url:
        # Thumbnail sejajar dengan blok statistik (di BAWAH judul, bukan di sebelah judul).
        children.append(
            discord.ui.Section(discord.ui.TextDisplay(stats_text), accessory=discord.ui.Thumbnail(media=d.avatar_url))
        )
    else:
        children.append(discord.ui.TextDisplay(stats_text))

    perks = style.perk_lines(st, d)
    progress = style.progress_lines(d)
    if perks:
        children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        children.append(discord.ui.TextDisplay("**\u2728 Info Eksklusif**\n" + "\n".join(perks)))
    if progress:
        children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        children.append(discord.ui.TextDisplay("\n".join(progress)))

    children.append(discord.ui.Separator(visible=False))
    children.append(discord.ui.TextDisplay(f"*{st.flair}*\n{components._footer_line()}"))
    if history_button and not d.excluded:
        children.append(discord.ui.ActionRow(StatsHistoryButton(d.user_id, disabled=d.orders <= 0)))
    return discord.ui.Container(*children, accent_colour=st.accent)


def build_card_view(d: style.CardData) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(build_card_container(d))
    return view


async def send_card(
    interaction: discord.Interaction, member: discord.abc.User, *, ephemeral: bool
) -> None:
    data = await stats_data.load_card_data(interaction.client, interaction.guild, member)
    await interaction.response.send_message(view=build_card_view(data), ephemeral=ephemeral)


# ============================================================================
# RIWAYAT PESANAN (publik)
# ============================================================================

def build_history_container(
    d: style.CardData, entries: list[tuple], page: int, pages: int, total: int, *, nav: bool = True
) -> discord.ui.Container:
    st = style.style_for(d.level, d.crown)
    header = (
        f"## \U0001F4DC Riwayat Pesanan\n-# {st.emoji} {d.name} \u00B7 {total} pesanan"
        f"{f' \u00B7 Halaman {page + 1}/{pages}' if pages > 1 else ''}"
    )
    if st.frame:
        header += f"\n-# {st.frame}"
    children: list = [
        discord.ui.TextDisplay(header),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay("\n\n".join(style.history_entry(o, items) for o, items in entries)),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
    ]
    summary = f"{style.rank_medal(d.rank)} " + (f"Peringkat **#{d.rank}**" if d.rank else "Belum berperingkat")
    summary += f" \u00B7 \U0001F4B0 Total spend **{style.money(d.spend, d.currency)}**"
    children.append(discord.ui.TextDisplay(summary))
    if nav and pages > 1:
        children.append(discord.ui.ActionRow(
            HistoryPageButton(-1, disabled=page <= 0), HistoryPageButton(+1, disabled=page >= pages - 1)
        ))
    return discord.ui.Container(*children, accent_colour=st.accent)


class HistoryPageButton(discord.ui.Button):
    def __init__(self, delta: int, *, disabled: bool) -> None:
        super().__init__(
            emoji="\u25C0\uFE0F" if delta < 0 else "\u25B6\uFE0F", style=_STYLE.secondary, disabled=disabled
        )
        self.delta = delta

    async def callback(self, interaction: discord.Interaction) -> None:
        view: HistoryView = self.view  # type: ignore[assignment]
        await view.go(interaction, view.page + self.delta)


class HistoryView(discord.ui.LayoutView):
    """Riwayat yang dikirim ke channel (keliatan semua orang). Cuma
    pemilik kartu yang bisa ganti halaman. Pas timeout, tombolnya dicopot
    biar gak ada tombol mati."""

    def __init__(self, owner_id: int, d: style.CardData, entries, page: int, pages: int, total: int) -> None:
        super().__init__(timeout=600)
        self.owner_id, self.d = owner_id, d
        self.page, self.pages, self.total, self.entries = page, pages, total, entries
        self.message: discord.Message | None = None
        self.add_item(build_history_container(d, entries, page, pages, total))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await _say(interaction, "Cuma pemilik riwayat ini yang bisa ganti halaman.")
            return False
        return True

    async def go(self, interaction: discord.Interaction, page: int) -> None:
        entries, total, pages = await stats_data.load_history_page(interaction.client.db, self.owner_id, page)
        page = max(0, min(page, pages - 1))
        new_view = HistoryView(self.owner_id, self.d, entries, page, pages, total)
        new_view.message = self.message
        await interaction.response.edit_message(view=new_view)

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        static = discord.ui.LayoutView(timeout=None)
        static.add_item(build_history_container(self.d, self.entries, self.page, self.pages, self.total, nav=False))
        try:
            await self.message.edit(view=static)
        except discord.HTTPException:
            pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        logger.exception("Error di riwayat pesanan.", exc_info=error)
        await _say(interaction, "Ada yang error pas ngeganti halaman. Coba lagi ya.")


# ============================================================================
# TOMBOL PERSISTEN
# ============================================================================

class StatsHistoryButton(discord.ui.DynamicItem[discord.ui.Button], template=r"noctra_stats_history:(?P<uid>\d+)"):
    """Kirim riwayat pesanan ke channel (publik). Cuma pemilik kartu yang
    bisa -- kartu orang lain boleh diliat, tapi riwayat belanjanya gak."""

    def __init__(self, user_id: int, *, disabled: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                label="Riwayat Pesanan", emoji="\U0001F4DC", style=_STYLE.primary,
                custom_id=f"noctra_stats_history:{user_id}", disabled=disabled,
            )
        )
        self.user_id = user_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(int(match["uid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.user_id:
            await _say(interaction, "Riwayat pesanan cuma bisa dikirim sama pemilik kartu ini.")
            return
        now = time.monotonic()
        wait = HISTORY_COOLDOWN - (now - _last_history.get(self.user_id, float("-inf")))
        if wait > 0:
            await _say(interaction, f"Sabar ya, tunggu {wait:.0f} detik lagi sebelum kirim riwayat lagi.")
            return
        db = interaction.client.db  # type: ignore[attr-defined]
        entries, total, pages = await stats_data.load_history_page(db, self.user_id, 0)
        if total == 0:
            await _say(interaction, "Belum ada pesanan selesai buat ditampilin.")
            return
        _last_history[self.user_id] = now
        data = await stats_data.load_card_data(interaction.client, interaction.guild, interaction.user)
        view = HistoryView(self.user_id, data, entries, 0, pages, total)
        # Publik: keliatan semua orang di channel yang sama.
        await interaction.response.send_message(view=view)
        try:
            view.message = await interaction.original_response()
        except discord.HTTPException:
            pass


class StatsOpenButton(discord.ui.DynamicItem[discord.ui.Button], template=r"noctra_stats_open"):
    """Tombol di panel publik: buka kartu statistik si penekan (privat)."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Cek Statistik Saya", emoji="\U0001F4CA", style=_STYLE.primary,
                custom_id="noctra_stats_open",
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        await send_card(interaction, interaction.user, ephemeral=True)


def build_entry_view() -> discord.ui.LayoutView:
    """Panel publik yang diposting staff (/stats_panel)."""
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay(
                "## \U0001F4CA Statistik Pembelian\n"
                "Cek kartu statistikmu: peringkat leaderboard, total pesanan, dan total spend.\n"
                "-# Semakin tinggi belanjamu, semakin eksklusif kartumu."
            ),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
            discord.ui.ActionRow(StatsOpenButton()),
            accent_colour=COLOR_ACCENT,
        )
    )
    return view
