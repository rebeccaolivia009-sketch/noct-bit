"""
UI interaktif checker akun Roblox (/tradecheck): modal input, tombol panel
publik, tombol "Cek Ulang", dan view hasilnya.

Dua tombol yang kepake lintas restart pake DynamicItem (pola sama kayak
RobloxSlideButton / GiveawayJoinButton): custom_id-nya nyimpen semua yang
dibutuhin, jadi cukup didaftarin CLASS-nya lewat bot.add_dynamic_items()
di bot.py -- gak perlu bot.add_view():

  * RobloxCheckPanelButton -- tombol "Cek Akun Saya" di panel publik, bisa
    DITEMPEL ke tempat lain juga (misal baris tombol katalog Roblox).
  * RobloxRecheckButton    -- "Cek Ulang" di kartu hasil.

Hasil pengecekan SELALU ephemeral (cuma keliatan si pengklik). Pembeli
dapet kartu versi pembeli; staff dapet kartu yang sama + "Catatan Staff"
(info teknis kayak cookie belum diatur) -- ditentuin dari izin ASLI
(is_staff), BUKAN dari isi custom_id biar gak bisa dipalsuin.
"""

from __future__ import annotations

import time

import discord

from bot.core.logger import logger
from bot.ui import components, embeds
from bot.utils.permissions import is_staff
from bot.utils.roblox_api import RobloxAPIError, RobloxRateLimited, RobloxUserNotFound
from bot.utils.roblox_trade_check import parse_roblox_identifier, result_to_card, run_trade_check

# Jeda antar pengecekan per orang (staff dikecualiin) -- nahan spam klik
# yang bisa bikin IP bot kena rate limit Roblox buat semua orang.
CHECK_COOLDOWN_SECONDS = 20.0
_last_check: dict[int, float] = {}


def _cooldown_remaining(user_id: int) -> float:
    elapsed = time.monotonic() - _last_check.get(user_id, 0.0)
    return max(CHECK_COOLDOWN_SECONDS - elapsed, 0.0)


def _start_cooldown(user_id: int) -> None:
    _last_check[user_id] = time.monotonic()
    # Rapihin entri basi biar dict gak tumbuh terus.
    if len(_last_check) > 2000:
        cutoff = time.monotonic() - CHECK_COOLDOWN_SECONDS
        for key in [k for k, v in _last_check.items() if v < cutoff]:
            _last_check.pop(key, None)


def _link_buttons(roblox_user_id: int) -> list[discord.ui.Button]:
    return [
        discord.ui.Button(
            label="Profil Roblox", style=discord.ButtonStyle.link,
            url=f"https://www.roblox.com/users/{roblox_user_id}/profile",
        ),
        discord.ui.Button(
            label="Rolimons", style=discord.ButtonStyle.link,
            url=f"https://www.rolimons.com/player/{roblox_user_id}",
        ),
    ]


class SendCheckToChannelButton(discord.ui.Button):
    """(Staff) Repost kartu versi PEMBELI (tanpa catatan staff) ke channel
    biasa -- misal ke ticket order, biar pembeli ikut liat hasilnya."""

    def __init__(self, card: dict) -> None:
        super().__init__(label="Kirim ke Channel Ini", style=discord.ButtonStyle.success)
        self.card = card

    async def callback(self, interaction: discord.Interaction) -> None:
        container = components.roblox_trade_check_container(self.card, staff_view=False)
        container.add_item(discord.ui.ActionRow(*_link_buttons(self.card["profile"]["id"])))
        try:
            await interaction.channel.send(view=components.NoctraLayout(container, timeout=None))
        except discord.HTTPException:
            await interaction.response.send_message(
                embed=embeds.error_embed("Gagal kirim kartu ke channel ini."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=embeds.success_embed("Kartu hasil udah dikirim ke channel ini."), ephemeral=True
        )


class TradeCheckResultView(discord.ui.LayoutView):
    def __init__(self, card: dict, *, staff_view: bool, post_to_channel: bool = False) -> None:
        super().__init__(timeout=600)
        container = components.roblox_trade_check_container(card, staff_view=staff_view)
        roblox_user_id = card["profile"]["id"]
        buttons: list[discord.ui.Item] = [RobloxRecheckButton(roblox_user_id), *_link_buttons(roblox_user_id)]
        if post_to_channel:
            buttons.append(SendCheckToChannelButton(card))
        container.add_item(discord.ui.ActionRow(*buttons))
        self.add_item(container)


async def perform_check(
    interaction: discord.Interaction, kind: str, value: str, *,
    force: bool = False, edit: bool = False, post_to_channel: bool = False,
) -> None:
    """Jalanin pengecekan lalu kirim hasilnya. `interaction` HARUS udah
    di-defer sama pemanggil. edit=True -> ganti kartu lama di tempat (buat
    tombol Cek Ulang); False -> kirim pesan ephemeral baru. Semua error
    ditangani di sini (pesan beda buat "gak ketemu" vs "Roblox bermasalah")."""
    async def fail(text: str) -> None:
        await interaction.followup.send(embed=embeds.error_embed(text), ephemeral=True)

    try:
        result = await run_trade_check(kind, value, force=force)
    except RobloxUserNotFound:
        label = f"ID `{value}`" if kind == "id" else f"username **{value}**"
        await fail(f"Akun Roblox dengan {label} tidak ditemukan. Cek lagi penulisannya.")
        return
    except RobloxRateLimited:
        await fail("Roblox lagi membatasi request. Coba lagi sekitar 1 menit.")
        return
    except RobloxAPIError as exc:
        logger.warning("Trade check gagal (%s %s): %s", kind, value, exc)
        await fail("Roblox lagi tidak bisa diakses. Coba lagi beberapa saat lagi.")
        return
    except Exception:  # noqa: BLE001
        logger.exception("Trade check error tak terduga (%s %s).", kind, value)
        await fail("Terjadi kesalahan saat mengecek akun. Coba lagi, atau hubungi staff kalau berulang.")
        return

    staff_view = await is_staff(interaction)
    view = TradeCheckResultView(result_to_card(result), staff_view=staff_view, post_to_channel=post_to_channel)
    if edit:
        await interaction.edit_original_response(view=view)
    else:
        await interaction.followup.send(view=view, ephemeral=True)


class RobloxCheckModal(discord.ui.Modal, title="Cek Akun Trade"):
    identifier = discord.ui.TextInput(
        label="Username atau link profil Roblox",
        placeholder="contoh: Builderman",
        min_length=3,
        max_length=120,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            kind, value = parse_roblox_identifier(str(self.identifier.value))
        except ValueError as exc:
            await interaction.response.send_message(embed=embeds.error_embed(str(exc)), ephemeral=True)
            return

        if not await is_staff(interaction):
            remaining = _cooldown_remaining(interaction.user.id)
            if remaining > 0:
                await interaction.response.send_message(
                    embed=embeds.error_embed(f"Tunggu {int(remaining) + 1} detik lagi sebelum cek ulang."),
                    ephemeral=True,
                )
                return
            _start_cooldown(interaction.user.id)

        await interaction.response.defer(ephemeral=True, thinking=True)
        await perform_check(interaction, kind, value)


class RobloxCheckPanelButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"noctra:rbxcheck:panel",
):
    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Cek Akun Saya", style=discord.ButtonStyle.primary, custom_id="noctra:rbxcheck:panel"
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction, item, match):  # noqa: D102
        return cls()

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RobloxCheckModal())


class RobloxRecheckButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"noctra:rbxcheck:again:(?P<user_id>[0-9]+)",
):
    def __init__(self, roblox_user_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Cek Ulang", style=discord.ButtonStyle.secondary,
                custom_id=f"noctra:rbxcheck:again:{roblox_user_id}",
            )
        )
        self.roblox_user_id = roblox_user_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):  # noqa: D102
        return cls(int(match["user_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await is_staff(interaction):
            remaining = _cooldown_remaining(interaction.user.id)
            if remaining > 0:
                await interaction.response.send_message(
                    embed=embeds.error_embed(f"Tunggu {int(remaining) + 1} detik lagi sebelum cek ulang."),
                    ephemeral=True,
                )
                return
            _start_cooldown(interaction.user.id)

        await interaction.response.defer()  # deferred update -- kartu lama diganti di tempat
        await perform_check(interaction, "id", str(self.roblox_user_id), force=True, edit=True)


def build_panel_view() -> discord.ui.LayoutView:
    """Panel publik "Cek Akun Trade" + tombolnya -- diposting lewat
    /tradecheck panel."""
    container = components.roblox_check_panel_container()
    container.add_item(discord.ui.ActionRow(RobloxCheckPanelButton()))
    return components.NoctraLayout(container, timeout=None)
