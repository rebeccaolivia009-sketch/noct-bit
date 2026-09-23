"""
Aksi channel ticket yang dipakai bareng.

Disentralisasi di sini (bukan di View atau Cog) biar tombol close,
command slash /ticket, dan background task auto-archive semua bisa
makai logic yang persis sama tanpa circular import antara bot.ui dan
bot.cogs.

Sadar `ticket_types` (/ticket type) -- tiap kind ticket BOLEH punya
kategori/log-channel/kategori-arsip sendiri kalau staff udah setup lewat
/ticket type add. Kalau belum diatur (atau kind-nya emang gak pernah
didaftarin, kayak "support" bawaan), fallback ke setting global
(RuntimeSettings) apa adanya -- jadi perilaku LAMA (sebelum fitur
ticket_types ada) tetep persis sama, gak ada yang berubah/rusak.
"""

from __future__ import annotations

import discord

from bot.core.logger import logger
from bot.database.queries import ticket_types as ticket_types_q
from bot.database.queries import tickets as tickets_q
from bot.ui import embeds
from bot.utils.helpers import RuntimeSettings
from bot.utils.transcript import build_html_transcript


async def create_ticket_channel(
    bot,
    guild: discord.Guild,
    user: discord.abc.User,
    kind: str,
    order_id: int | None = None,
    name_hint: str | None = None,
) -> discord.TextChannel:
    db = bot.db
    runtime = RuntimeSettings(db)

    ticket_type = await ticket_types_q.get_by_slug(db, guild.id, kind)

    category_id = (ticket_type["category_id"] if ticket_type else None) or await runtime.ticket_category_id()
    category = guild.get_channel(category_id) if category_id else None
    if not isinstance(category, discord.CategoryChannel):
        category = None

    staff_role_id = await runtime.staff_role_id()

    overwrites: dict = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, manage_channels=True, attach_files=True
        ),
    }
    member = guild.get_member(user.id) if hasattr(guild, "get_member") else None
    target = member or user
    overwrites[target] = discord.PermissionOverwrite(
        view_channel=True, send_messages=True, attach_files=True, read_message_history=True
    )
    if staff_role_id:
        role = guild.get_role(staff_role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True, send_messages=True, attach_files=True, read_message_history=True
            )

    base_name = name_hint or f"{kind}-{getattr(user, 'name', 'user')}"
    channel_name = f"{kind}-{order_id}-{base_name}" if order_id else base_name
    channel_name = channel_name.lower().replace(" ", "-")[:90]

    channel = await guild.create_text_channel(
        channel_name,
        category=category,
        overwrites=overwrites,
        reason=f"Ticket NOCTRA ({kind}) buat {user}",
    )
    await tickets_q.create_ticket(db, user.id, channel.id, kind, order_id)
    logger.info("Ticket channel dibuat #%s (%s) buat %s", channel.name, kind, user)
    return channel


async def close_ticket(
    bot,
    channel: discord.TextChannel,
    closed_by_display: str,
    reason: str | None,
    auto: bool = False,
) -> None:
    db = bot.db
    runtime = RuntimeSettings(db)

    ticket = await tickets_q.get_ticket_by_channel(db, channel.id)
    status = "archived" if auto else "closed"
    await tickets_q.set_ticket_status(db, channel.id, status, reason)

    ticket_type = (
        await ticket_types_q.get_by_slug(db, channel.guild.id, ticket["kind"]) if ticket else None
    )

    try:
        transcript_file = await build_html_transcript(channel)
    except Exception:  # noqa: BLE001
        logger.exception("Gagal bikin transcript buat #%s", channel.name)
        transcript_file = None

    log_channel_id = (ticket_type["log_channel_id"] if ticket_type else None) or await runtime.ticket_log_channel_id()
    if log_channel_id:
        log_channel = bot.get_channel(log_channel_id)
        if isinstance(log_channel, discord.TextChannel):
            embed = embeds.ticket_closed_embed(reason, closed_by_display)
            embed.add_field(name="Channel", value=channel.mention, inline=True)
            try:
                if transcript_file:
                    transcript_file.fp.seek(0)
                await log_channel.send(
                    embed=embed,
                    file=transcript_file if transcript_file else discord.utils.MISSING,
                )
            except discord.HTTPException:
                logger.exception("Gagal posting transcript ke log channel.")

    closed_embed = embeds.ticket_closed_embed(reason, closed_by_display)
    if ticket:
        try:
            await channel.send(
                embed=closed_embed,
                view=_ReopenViewRef.get(),
            )
        except discord.HTTPException:
            pass

    # Kunci channel buat pemilik ticket; staff tetep bisa akses lewat overwrite.
    if ticket:
        try:
            owner = channel.guild.get_member(ticket["user_id"])
            if owner:
                await channel.set_permissions(
                    owner, view_channel=True, send_messages=False, read_message_history=True
                )
        except discord.HTTPException:
            logger.exception("Gagal kunci channel #%s", channel.name)

    # Pindahin/rename channel-nya biar keliatan jelas kalau ticket yang
    # udah ditutup gak cuma diem di tempat yang sama tanpa perubahan --
    # ini berlaku BAIK buat klik manual "Close Ticket" MAUPUN task
    # auto-archive karena inaktif, bukan cuma yang belakangan.
    archive_category_id = (
        ticket_type["archive_category_id"] if ticket_type else None
    ) or await runtime.ticket_archive_category_id()
    if archive_category_id:
        archive_category = channel.guild.get_channel(archive_category_id)
        if isinstance(archive_category, discord.CategoryChannel):
            try:
                await channel.edit(category=archive_category)
            except discord.HTTPException:
                logger.exception("Gagal pindahin #%s ke kategori archive.", channel.name)

    if not channel.name.startswith("closed-"):
        try:
            await channel.edit(name=f"closed-{channel.name}"[:100])
        except discord.HTTPException:
            logger.exception("Gagal rename #%s pas close.", channel.name)


async def reopen_ticket(bot, channel: discord.TextChannel, reopened_by_display: str) -> None:
    db = bot.db
    runtime = RuntimeSettings(db)
    ticket = await tickets_q.get_ticket_by_channel(db, channel.id)
    if not ticket:
        return
    await tickets_q.set_ticket_status(db, channel.id, "open")
    owner = channel.guild.get_member(ticket["user_id"])
    if owner:
        try:
            await channel.set_permissions(
                owner, view_channel=True, send_messages=True, read_message_history=True
            )
        except discord.HTTPException:
            pass

    # Balikin penanda close yang keliatan: hapus prefix "closed-" dan
    # pindah balik ke kategori ticket biasa kalau diatur.
    if channel.name.startswith("closed-"):
        try:
            await channel.edit(name=channel.name.removeprefix("closed-")[:100])
        except discord.HTTPException:
            logger.exception("Gagal rename #%s pas reopen.", channel.name)

    ticket_type = await ticket_types_q.get_by_slug(db, channel.guild.id, ticket["kind"])
    ticket_category_id = (ticket_type["category_id"] if ticket_type else None) or await runtime.ticket_category_id()
    if ticket_category_id:
        ticket_category = channel.guild.get_channel(ticket_category_id)
        if isinstance(ticket_category, discord.CategoryChannel):
            try:
                await channel.edit(category=ticket_category)
            except discord.HTTPException:
                pass

    await channel.send(
        embed=embeds.info_embed("Ticket Dibuka Lagi", f"Dibuka lagi sama **{reopened_by_display}**.")
    )


class _ReopenViewRef:
    """Penampung lazy buat ngindarin circular import antara module ini dan bot.ui.views."""

    _view = None

    @classmethod
    def set(cls, view) -> None:
        cls._view = view

    @classmethod
    def get(cls):
        return cls._view
