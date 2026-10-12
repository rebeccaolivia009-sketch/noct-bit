"""
Pengingat SLA staff + pelacak respon ticket.

Tiap 2 menit bot ngecek apa ada yang kelamaan nunggu tindakan staff, terus
ngirim SATU pesan pengingat ke channel order-log (nge-ping role staff) dan
ngapus pengingat sebelumnya biar channel gak numpuk. Tiga jenis (aturan
lengkap & kenapa jam hitungnya cuma jam toko buka: bot.utils.sla):

  * Bukti bayar belum diverifikasi   (default 10 menit)
  * Order lunas belum selesai        (default 60 menit)
  * Ticket belum dibalas staff       (default 10 menit)

Command staff:
  /sla status                     -- pengaturan sekarang + yang lagi nunggu
  /sla set [bukti_menit] [order_menit] [ticket_menit] [ulang_menit] [maks]
                                  -- ubah batas (0 = matiin jenis itu)
  /sla channel [channel]          -- channel pengingat (kosong = order-log)
  /sla aktif aktif:<bool>         -- nyalain/matiin semua pengingat SLA
  /sla bayar menit:<n>            -- DM pembeli n menit sebelum batas bayar
                                     (0 = mati; ditangani bot.cogs.payment_reminder)

Cog ini juga nyatet balasan staff PERTAMA di tiap ticket (dasar statistik
waktu respon di /staffstats) lewat listener on_message.
"""

from __future__ import annotations

import dataclasses
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.core.logger import logger
from bot.database.queries import settings as settings_q
from bot.database.queries import staff_metrics as metrics_q
from bot.ui import components, embeds
from bot.utils import sla as sla_util
from bot.utils.helpers import RuntimeSettings, format_price
from bot.utils.permissions import staff_only
from bot.utils.store_status import compute_state

LAST_ALERT_CHANNEL_KEY = "sla_last_alert_channel_id"
LAST_ALERT_MESSAGE_KEY = "sla_last_alert_message_id"
CATEGORY_CACHE_SECONDS = 60.0


class SlaCog(commands.Cog):
    sla_group = app_commands.Group(name="sla", description="Pengingat SLA staff.", guild_only=True)

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._category_cache: tuple[float, int | None] = (0.0, None)
        self._category_cache_valid = False

    async def cog_load(self) -> None:
        await metrics_q.ensure_tables(self.bot.db)
        self.check_loop.start()

    async def cog_unload(self) -> None:
        self.check_loop.cancel()

    # -- Util --------------------------------------------------------------------------

    async def _alert_channel(self, cfg: sla_util.SlaConfig) -> discord.TextChannel | None:
        channel_id = cfg.channel_id or await RuntimeSettings(self.bot.db).order_log_channel_id()
        channel = self.bot.get_channel(channel_id) if channel_id else None
        return channel if isinstance(channel, discord.TextChannel) else None

    def _build_card(self, alerts: list[sla_util.Alert], cfg: sla_util.SlaConfig, role_id: int | None) -> dict:
        def suffix(alert: sla_util.Alert) -> str:
            return f"  \u00b7  pengingat {alert.reminders_sent + 1}/{cfg.max_reminders}"

        sections = []
        for kind, title in (
            ("proof", "Bukti Bayar Belum Diverifikasi"),
            ("order", "Order Lunas Belum Selesai"),
            ("ticket", "Ticket Belum Dibalas"),
        ):
            lines = []
            for alert in (a for a in alerts if a.kind == kind):
                waited = sla_util.format_duration(alert.waited)
                if kind == "ticket":
                    lines.append(f"\u25b8 <#{alert.channel_id}> \u00b7 <@{alert.user_id}> \u00b7 belum dibalas {waited}{suffix(alert)}")
                else:
                    price = format_price(alert.amount, alert.currency or "") if alert.amount is not None else ""
                    lines.append(f"\u25b8 #{alert.ref_id} \u00b7 <@{alert.user_id}> \u00b7 {price} \u00b7 menunggu {waited}{suffix(alert)}")
            if lines:
                sections.append({"title": title, "lines": lines})
        return {
            "role_id": role_id, "sections": sections, "total": len(alerts),
            "round_note": (
                f"Hitungan hanya jam toko buka. Tiap item diingatkan maksimal {cfg.max_reminders}x, "
                f"jeda {cfg.repeat_minutes} menit."
            ),
        }

    # -- Loop pengingat -------------------------------------------------------------------

    @tasks.loop(minutes=2)
    async def check_loop(self) -> None:
        try:
            await self.run_check()
        except Exception:  # noqa: BLE001
            logger.exception("Error di pengingat SLA.")

    @check_loop.before_loop
    async def _before_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def run_check(self) -> int:
        """Satu putaran pengecekan. Return jumlah item yang diingatkan."""
        db = self.bot.db
        cfg = await sla_util.load_config(db)
        if not cfg.enabled:
            return 0
        runtime = RuntimeSettings(db)
        open_time, close_time = await runtime.store_status_open_time(), await runtime.store_status_close_time()
        # Toko tutup / lagi perbaikan sistem -> gak ada yang diingatkan.
        if compute_state(open_time, close_time) == "closed" or await runtime.store_status_special() == "maintenance":
            return 0

        alerts = await sla_util.collect_alerts(db, cfg, open_time, close_time)
        if not alerts:
            return 0
        channel = await self._alert_channel(cfg)
        if channel is None:
            return 0  # belum ada channel order-log/SLA yang diatur

        role_id = await runtime.staff_role_id()
        card = self._build_card(alerts, cfg, role_id)
        view = components.NoctraLayout(components.sla_alert_container(card), timeout=None)
        mentions = discord.AllowedMentions(roles=[discord.Object(id=role_id)] if role_id else False, users=False)
        try:
            sent = await channel.send(view=view, allowed_mentions=mentions)
        except discord.HTTPException:
            logger.exception("Gagal kirim pengingat SLA.")
            return 0  # jangan hitung sebagai udah diingatkan

        for alert in alerts:
            await metrics_q.bump_sla_state(db, alert.kind, alert.ref_id)

        # Hapus pengingat sebelumnya -- cuma yang terbaru yang relevan.
        try:
            old_channel_id = await settings_q.get_setting(db, LAST_ALERT_CHANNEL_KEY)
            old_message_id = await settings_q.get_setting(db, LAST_ALERT_MESSAGE_KEY)
            if old_channel_id and old_message_id:
                old_channel = self.bot.get_channel(int(old_channel_id))
                if isinstance(old_channel, discord.TextChannel):
                    await (await old_channel.fetch_message(int(old_message_id))).delete()
        except discord.HTTPException:
            pass
        await settings_q.set_setting(db, LAST_ALERT_CHANNEL_KEY, str(channel.id))
        await settings_q.set_setting(db, LAST_ALERT_MESSAGE_KEY, str(sent.id))
        return len(alerts)

    # -- Respon pertama ticket --------------------------------------------------------------

    async def _ticket_category_id(self) -> int | None:
        now = time.monotonic()
        stamp, value = self._category_cache
        if self._category_cache_valid and now - stamp < CATEGORY_CACHE_SECONDS:
            return value
        value = await RuntimeSettings(self.bot.db).ticket_category_id()
        self._category_cache, self._category_cache_valid = (now, value), True
        return value

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not isinstance(message.channel, discord.TextChannel):
            return
        try:
            category_id = await self._ticket_category_id()
            if not category_id or message.channel.category_id != category_id:
                return  # bukan channel ticket -- keluar cepat tanpa nyentuh DB
            author = message.author
            if not isinstance(author, discord.Member):
                return
            staff_role_id = await RuntimeSettings(self.bot.db).staff_role_id()
            is_staff = author.guild_permissions.administrator or (
                staff_role_id is not None and any(role.id == staff_role_id for role in author.roles)
            )
            if not is_staff:
                return
            ticket = await metrics_q.get_open_ticket_by_channel(self.bot.db, message.channel.id)
            if ticket is None or ticket["user_id"] == author.id:
                return
            await metrics_q.record_first_response(self.bot.db, ticket["id"], author.id)
        except Exception:  # noqa: BLE001
            logger.warning("Gagal nyatet respon pertama ticket.")

    # -- Command ----------------------------------------------------------------------------------

    @sla_group.command(name="status", description="Pengaturan SLA sekarang + yang lagi menunggu.")
    @staff_only()
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        db = self.bot.db
        cfg = await sla_util.load_config(db)
        runtime = RuntimeSettings(db)
        open_time, close_time = await runtime.store_status_open_time(), await runtime.store_status_close_time()
        # Semua yang melewati batas SEKARANG, tanpa melihat sudah berapa kali diingatkan.
        probe = dataclasses.replace(cfg, repeat_minutes=0, max_reminders=10**6)
        waiting = await sla_util.collect_alerts(db, probe, open_time, close_time)
        counts = {k: sum(1 for a in waiting if a.kind == k) for k in ("proof", "order", "ticket")}
        channel = await self._alert_channel(cfg)

        def limit(minutes: int) -> str:
            return f"{minutes} menit" if minutes > 0 else "dimatikan"

        paused = compute_state(open_time, close_time) == "closed" or await runtime.store_status_special() == "maintenance"
        lines = [
            f"\u25b8 **Status:** {'AKTIF' if cfg.enabled else 'MATI'}" + (" (sedang jeda: toko tutup/perbaikan)" if cfg.enabled and paused else ""),
            f"\u25b8 **Bukti bayar belum diverifikasi:** batas {limit(cfg.proof_minutes)} -- menunggu sekarang: {counts['proof']}",
            f"\u25b8 **Order lunas belum selesai:** batas {limit(cfg.order_minutes)} -- menunggu sekarang: {counts['order']}",
            f"\u25b8 **Ticket belum dibalas:** batas {limit(cfg.ticket_minutes)} -- menunggu sekarang: {counts['ticket']}",
            f"\u25b8 **Pengulangan:** maks {cfg.max_reminders}x, jeda {cfg.repeat_minutes} menit",
            f"\u25b8 **Pengingat bayar ke pembeli:** {limit(cfg.pay_reminder_minutes)} sebelum batas",
            f"\u25b8 **Channel:** {channel.mention if channel else 'belum diatur (set /settings order-log atau /sla channel)'}",
            "-# Jam hitung hanya jam toko buka. Item lebih dari 24 jam diabaikan.",
        ]
        await interaction.followup.send(embed=embeds.info_embed("SLA Staff", "\n".join(lines)), ephemeral=True)

    @sla_group.command(name="set", description="Ubah batas SLA (menit). 0 = matikan jenis itu.")
    @app_commands.describe(
        bukti_menit="Bukti bayar belum diverifikasi lebih dari ... menit", order_menit="Order lunas belum selesai lebih dari ... menit",
        ticket_menit="Ticket belum dibalas lebih dari ... menit", ulang_menit="Jeda antar pengingat untuk item yang sama",
        maks="Maksimal pengingat per item",
    )
    @staff_only()
    async def set_limits(
        self, interaction: discord.Interaction,
        bukti_menit: app_commands.Range[int, 0, 1440] | None = None, order_menit: app_commands.Range[int, 0, 1440] | None = None,
        ticket_menit: app_commands.Range[int, 0, 1440] | None = None, ulang_menit: app_commands.Range[int, 1, 1440] | None = None,
        maks: app_commands.Range[int, 1, 20] | None = None,
    ) -> None:
        updates = {
            "sla_proof_minutes": bukti_menit, "sla_order_minutes": order_menit, "sla_ticket_minutes": ticket_menit,
            "sla_repeat_minutes": ulang_menit, "sla_max_reminders": maks,
        }
        changed = {k: v for k, v in updates.items() if v is not None}
        if not changed:
            await interaction.response.send_message(embed=embeds.error_embed("Isi minimal satu parameter yang mau diubah."), ephemeral=True)
            return
        for key, value in changed.items():
            await settings_q.set_setting(self.bot.db, key, str(value))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Pengaturan SLA diperbarui ({len(changed)} nilai). Lihat hasilnya di `/sla status`."), ephemeral=True
        )

    @sla_group.command(name="channel", description="Channel tujuan pengingat SLA (kosong = channel order-log).")
    @staff_only()
    async def channel(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        await settings_q.set_setting(self.bot.db, "sla_channel_id", str(channel.id) if channel else "")
        text = f"Pengingat SLA dikirim ke {channel.mention}." if channel else "Pengingat SLA kembali ke channel order-log."
        await interaction.response.send_message(embed=embeds.success_embed(text), ephemeral=True)

    @sla_group.command(name="aktif", description="Nyalakan/matikan semua pengingat SLA.")
    @staff_only()
    async def aktif(self, interaction: discord.Interaction, aktif: bool) -> None:
        await settings_q.set_setting(self.bot.db, "sla_enabled", "1" if aktif else "0")
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Pengingat SLA **{'AKTIF' if aktif else 'MATI'}**."), ephemeral=True
        )

    @sla_group.command(name="bayar", description="DM pembeli sekian menit sebelum batas bayar habis (0 = mati).")
    @app_commands.describe(menit="Berapa menit sebelum batas bayar pembeli diingatkan")
    @staff_only()
    async def bayar(self, interaction: discord.Interaction, menit: app_commands.Range[int, 0, 240]) -> None:
        await settings_q.set_setting(self.bot.db, "pay_reminder_minutes", str(menit))
        text = f"Pembeli akan diingatkan {menit} menit sebelum batas bayar." if menit else "Pengingat batas bayar ke pembeli dimatikan."
        await interaction.response.send_message(embed=embeds.success_embed(text), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SlaCog(bot))
