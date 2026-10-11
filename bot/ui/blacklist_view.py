"""
Panel daftar blacklist (staff): satu pesan Components V2 yang bisa dibuka
per halaman lewat tombol Sebelumnya/Selanjutnya, dan di-update OTOMATIS
(edit in-place) tiap ada yang ditambah/dihapus dari blacklist.

Tombol halaman pake DynamicItem (custom_id nyimpen nomor halaman tujuan),
jadi tetep jalan abis bot restart -- cukup daftarin CLASS-nya di bot.py.
Cuma staff yang bisa mencet tombolnya.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import discord

from bot.core.logger import logger
from bot.database.queries import blacklist as blacklist_q
from bot.database.queries import settings as settings_q
from bot.ui import components, embeds
from bot.utils.permissions import is_staff

PAGE_SIZE = 8
PANEL_CHANNEL_KEY = "blacklist_panel_channel_id"
PANEL_MESSAGE_KEY = "blacklist_panel_message_id"


def _to_unix(sqlite_utc: str) -> int:
    try:
        return int(datetime.strptime(sqlite_utc, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
    except (TypeError, ValueError):
        return int(datetime.now(timezone.utc).timestamp())


class BlacklistPageButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"noctra:blacklist:page:(?P<page>[0-9]+)",
):
    def __init__(self, page: int, label: str = "Halaman") -> None:
        super().__init__(
            discord.ui.Button(
                label=label, style=discord.ButtonStyle.secondary, custom_id=f"noctra:blacklist:page:{page}"
            )
        )
        self.page = page

    @classmethod
    async def from_custom_id(cls, interaction, item, match):  # noqa: D102
        return cls(int(match["page"]), label=item.label or "Halaman")

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await is_staff(interaction):
            await interaction.response.send_message(
                embed=embeds.error_embed("Cuma staff yang bisa membuka panel ini."), ephemeral=True
            )
            return
        db = interaction.client.db  # type: ignore[attr-defined]
        await interaction.response.edit_message(view=await build_panel_view(db, self.page))


async def build_panel_view(db, page: int = 0) -> discord.ui.LayoutView:
    total = await blacklist_q.count_entries(db)
    total_pages = max(1, math.ceil(total / PAGE_SIZE))
    page = max(0, min(page, total_pages - 1))
    rows = await blacklist_q.list_entries(db, PAGE_SIZE, page * PAGE_SIZE)
    entries = [
        {
            "number": page * PAGE_SIZE + index, "user_id": row["user_id"], "reason": row["reason"],
            "added_by": row["added_by"], "created_ts": _to_unix(row["created_at"]),
        }
        for index, row in enumerate(rows, start=1)
    ]
    container = components.blacklist_panel_container(entries, page, total_pages, total)
    buttons: list[discord.ui.Item] = []
    if page > 0:
        buttons.append(BlacklistPageButton(page - 1, "Sebelumnya"))
    if page < total_pages - 1:
        buttons.append(BlacklistPageButton(page + 1, "Selanjutnya"))
    if buttons:
        container.add_item(discord.ui.ActionRow(*buttons))
    return components.NoctraLayout(container, timeout=None)


async def refresh_panel(bot) -> None:
    """Update panel blacklist yang udah diposting (kalau ada) ke halaman
    pertama -- dipanggil tiap blacklist berubah. Best-effort."""
    db = bot.db
    try:
        channel_id = await settings_q.get_setting(db, PANEL_CHANNEL_KEY)
        message_id = await settings_q.get_setting(db, PANEL_MESSAGE_KEY)
        if not channel_id or not message_id:
            return
        channel = bot.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        message = await channel.fetch_message(int(message_id))
        await message.edit(view=await build_panel_view(db, 0))
    except discord.NotFound:
        pass  # panel kehapus manual -- /blacklist panel bikin lagi
    except Exception:  # noqa: BLE001
        logger.warning("Gagal refresh panel blacklist.")


async def post_panel(bot, channel: discord.TextChannel) -> discord.Message:
    """Posting panel baru di `channel` dan catat sebagai panel aktif --
    panel lama (kalau ada) diganti, jadi cuma ada satu panel live."""
    db = bot.db
    old_channel_id = await settings_q.get_setting(db, PANEL_CHANNEL_KEY)
    old_message_id = await settings_q.get_setting(db, PANEL_MESSAGE_KEY)
    message = await channel.send(view=await build_panel_view(db, 0))
    await settings_q.set_setting(db, PANEL_CHANNEL_KEY, str(channel.id))
    await settings_q.set_setting(db, PANEL_MESSAGE_KEY, str(message.id))
    if old_channel_id and old_message_id:
        try:
            old_channel = bot.get_channel(int(old_channel_id))
            if isinstance(old_channel, discord.TextChannel):
                old_message = await old_channel.fetch_message(int(old_message_id))
                await old_message.delete()
        except discord.HTTPException:
            pass
    return message
