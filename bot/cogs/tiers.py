"""
Command: /tier -- tier loyalitas pembeli (mis. Royal Buyer, King of Noctra).
Tiap tier = nama + role Discord + minimal total belanja; role dikasih
OTOMATIS begitu belanja pembeli nyampe ambangnya. Aturan lengkap (cuma naik,
akun staff dikecualiin, dst) ada di bot.utils.tiers.

Staff:
  /tier add nama:<> role:<> min_spend:<> [deskripsi]  -- tambah tier
  /tier edit nama:<> [nama_baru] [role] [min_spend] [deskripsi]
  /tier remove nama:<>
  /tier list                      -- semua tier + jumlah pemegangnya
  /tier sync [apply]              -- SIMULASI dulu (apply=False): lihat siapa
        yang bakal naik tier TANPA ngubah role apapun. apply=True baru nerapin.
  /tier auto aktif:<bool>         -- nyalain/matiin otomatisasi (default MATI)
  /tier panel                     -- posting panel publik daftar tier
Semua pembeli:
  /tier me                        -- progres tier sendiri (ephemeral)

Urutan rollout yang aman: /tier add (semua tier) -> /tier sync (simulasi) ->
cek hasilnya -> /tier sync apply:True -> /tier auto aktif:True.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.core.logger import logger
from bot.database.queries import buyer_stats as buyer_stats_q
from bot.database.queries import loyalty_tiers as tiers_q
from bot.database.queries import settings as settings_q
from bot.ui import components, embeds
from bot.utils import tiers as tiers_util
from bot.utils.helpers import format_price
from bot.utils.permissions import staff_only

PANEL_CHANNEL_KEY = "tier_panel_channel_id"
PANEL_MESSAGE_KEY = "tier_panel_message_id"
MAX_PREVIEW_LINES = 15
AUTO_SYNC_HOURS = 6


class TiersCog(commands.Cog):
    tier_group = app_commands.Group(name="tier", description="Tier loyalitas pembeli.", guild_only=True)

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        await tiers_q.ensure_tables(self.bot.db)
        self.auto_sync_loop.start()

    async def cog_unload(self) -> None:
        self.auto_sync_loop.cancel()

    # -- Util ----------------------------------------------------------------------

    async def _currency(self) -> str:
        from bot.core.config import config

        return await buyer_stats_q.get_store_currency(self.bot.db) or config.default_currency

    async def _refresh_panel(self, guild: discord.Guild) -> None:
        """Update panel publik yang udah diposting (kalau ada) -- dipanggil
        tiap daftar tier berubah. Best-effort."""
        db = self.bot.db
        try:
            channel_id = await settings_q.get_setting(db, PANEL_CHANNEL_KEY)
            message_id = await settings_q.get_setting(db, PANEL_MESSAGE_KEY)
            if not channel_id or not message_id:
                return
            channel = self.bot.get_channel(int(channel_id))
            if not isinstance(channel, discord.TextChannel):
                return
            message = await channel.fetch_message(int(message_id))
            await message.edit(view=await self._panel_view(guild))
        except discord.NotFound:
            pass
        except Exception:  # noqa: BLE001
            logger.warning("Gagal refresh panel tier.")

    async def _panel_view(self, guild: discord.Guild) -> discord.ui.LayoutView:
        tiers = await tiers_util.load_tiers(self.bot.db, guild.id)
        container = components.tier_panel_container(
            [{"name": t.name, "role_id": t.role_id, "min_spend": t.min_spend, "description": t.description} for t in tiers],
            await self._currency(),
        )
        return components.NoctraLayout(container, timeout=None)

    # -- Konfigurasi -------------------------------------------------------------------

    @tier_group.command(name="add", description="Tambah tier loyalitas (nama + role + minimal total belanja).")
    @app_commands.describe(
        nama="Nama tier, misal Royal Buyer", role="Role Discord yang dikasih untuk tier ini",
        min_spend="Minimal total belanja (angka, sesuai mata uang toko)",
        deskripsi="(opsional) Keuntungan/keterangan tier, tampil di kartu & panel",
    )
    @staff_only()
    async def add(
        self, interaction: discord.Interaction, nama: app_commands.Range[str, 2, 40], role: discord.Role,
        min_spend: app_commands.Range[float, 0, 1_000_000_000_000], deskripsi: app_commands.Range[str, 1, 200] | None = None,
    ) -> None:
        db, guild = self.bot.db, interaction.guild
        problem = tiers_util.role_problem(guild, role)
        if problem:
            await interaction.response.send_message(embed=embeds.error_embed(f"Role {role.mention} gak bisa dipakai: {problem}."), ephemeral=True)
            return
        if await tiers_q.get_tier(db, guild.id, nama):
            await interaction.response.send_message(embed=embeds.error_embed(f"Tier **{nama}** sudah ada. Pakai `/tier edit`."), ephemeral=True)
            return
        if any(t.role_id == role.id for t in await tiers_util.load_tiers(db, guild.id)):
            await interaction.response.send_message(embed=embeds.error_embed(f"Role {role.mention} sudah dipakai tier lain."), ephemeral=True)
            return
        await tiers_q.add_tier(db, guild.id, nama, role.id, float(min_spend), deskripsi)
        await self._refresh_panel(guild)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Tier **{nama}** ditambahkan: {role.mention}, minimal belanja {format_price(float(min_spend), await self._currency())}."),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )

    @tier_group.command(name="edit", description="Ubah tier (kosongkan parameter yang tidak diubah).")
    @app_commands.describe(
        nama="Tier yang diubah", nama_baru="Nama baru", role="Role baru", min_spend="Minimal belanja baru",
        deskripsi="Deskripsi baru (isi `-` untuk menghapus)",
    )
    @staff_only()
    async def edit(
        self, interaction: discord.Interaction, nama: str, nama_baru: app_commands.Range[str, 2, 40] | None = None,
        role: discord.Role | None = None, min_spend: app_commands.Range[float, 0, 1_000_000_000_000] | None = None,
        deskripsi: app_commands.Range[str, 1, 200] | None = None,
    ) -> None:
        db, guild = self.bot.db, interaction.guild
        tier = await tiers_q.get_tier(db, guild.id, nama)
        if tier is None:
            await interaction.response.send_message(embed=embeds.error_embed(f"Tier **{nama}** gak ketemu."), ephemeral=True)
            return
        if role is not None:
            problem = tiers_util.role_problem(guild, role)
            if problem:
                await interaction.response.send_message(embed=embeds.error_embed(f"Role {role.mention} gak bisa dipakai: {problem}."), ephemeral=True)
                return
            if any(t.role_id == role.id and t.id != tier["id"] for t in await tiers_util.load_tiers(db, guild.id)):
                await interaction.response.send_message(embed=embeds.error_embed(f"Role {role.mention} sudah dipakai tier lain."), ephemeral=True)
                return
        if nama_baru and nama_baru.lower() != nama.lower() and await tiers_q.get_tier(db, guild.id, nama_baru):
            await interaction.response.send_message(embed=embeds.error_embed(f"Tier **{nama_baru}** sudah ada."), ephemeral=True)
            return
        await tiers_q.update_tier(
            db, tier["id"], name=nama_baru, role_id=role.id if role else None,
            min_spend=float(min_spend) if min_spend is not None else None,
            description=("" if deskripsi == "-" else deskripsi),
        )
        await self._refresh_panel(guild)
        await interaction.response.send_message(embed=embeds.success_embed(f"Tier **{tier['name']}** diperbarui."), ephemeral=True)

    @tier_group.command(name="remove", description="Hapus tier (role Discord-nya tidak ikut dihapus).")
    @app_commands.describe(nama="Tier yang dihapus")
    @staff_only()
    async def remove(self, interaction: discord.Interaction, nama: str) -> None:
        db, guild = self.bot.db, interaction.guild
        tier = await tiers_q.get_tier(db, guild.id, nama)
        if tier is None:
            await interaction.response.send_message(embed=embeds.error_embed(f"Tier **{nama}** gak ketemu."), ephemeral=True)
            return
        await tiers_q.remove_tier(db, tier["id"])
        await self._refresh_panel(guild)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Tier **{tier['name']}** dihapus. Role <@&{tier['role_id']}> tetap ada di server dan di user yang memegangnya."),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )

    @tier_group.command(name="list", description="Semua tier, ambangnya, dan jumlah pemegang rolenya.")
    @staff_only()
    async def list_tiers(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        tiers = await tiers_util.load_tiers(self.bot.db, guild.id)
        if not tiers:
            await interaction.response.send_message(
                embed=embeds.info_embed("Tier Loyalitas", "Belum ada tier. Tambah lewat `/tier add`."), ephemeral=True
            )
            return
        currency = await self._currency()
        auto = await tiers_util.is_auto_enabled(self.bot.db)
        lines = []
        for tier in reversed(tiers):
            role = guild.get_role(tier.role_id)
            holders = len(role.members) if role else 0
            note = "" if role else "  **(role hilang!)**"
            lines.append(f"\u25b8 **{tier.name}** -- {role.mention if role else f'`{tier.role_id}`'} -- min {format_price(tier.min_spend, currency)} -- {holders} pemegang{note}")
        lines.append(f"\nOtomatisasi: **{'AKTIF' if auto else 'MATI'}**")
        await interaction.response.send_message(
            embed=embeds.info_embed("Tier Loyalitas", "\n".join(lines)), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    # -- Sinkronisasi ---------------------------------------------------------------------

    @tier_group.command(name="sync", description="Hitung & (opsional) terapkan tier semua pembeli. Default: SIMULASI saja.")
    @app_commands.describe(apply="False = simulasi (tidak mengubah role). True = terapkan perubahan role.")
    @staff_only()
    async def sync(self, interaction: discord.Interaction, apply: bool = False) -> None:
        guild = interaction.guild
        await interaction.response.defer(ephemeral=True, thinking=True)
        results = await tiers_util.sync_all(self.bot, guild, apply=apply)
        if not results:
            await interaction.followup.send(
                embed=embeds.info_embed("Sinkronisasi Tier", "Belum ada tier atau belum ada pembeli yang punya belanja."), ephemeral=True
            )
            return

        currency = await self._currency()
        tiers = {t.role_id: t for t in await tiers_util.load_tiers(self.bot.db, guild.id)}
        changes = [r for r in results if r.plan and r.plan.has_changes]
        errors = [r for r in results if r.error]
        manual_higher = [r for r in results if r.plan and r.plan.kept_manual_higher]
        applied = [r for r in results if r.applied]

        lines = [
            f"Dicek: **{len(results)}** pembeli",
            f"{'Diterapkan' if apply else 'Akan berubah'}: **{len(applied) if apply else len(changes)}**",
            f"Role manual lebih tinggi (tidak disentuh): **{len(manual_higher)}**",
            f"Gagal/dilewati: **{len(errors)}**",
        ]
        shown = changes[:MAX_PREVIEW_LINES]
        if shown:
            lines.append("")
            for r in shown:
                parts = []
                if r.plan.add_role_id:
                    parts.append(f"+ {tiers[r.plan.add_role_id].name}")
                parts.extend(f"- {tiers[rid].name}" for rid in r.plan.remove_role_ids if rid in tiers)
                lines.append(f"\u25b8 <@{r.user_id}> ({format_price(r.spend, currency)}): {', '.join(parts)}")
            if len(changes) > len(shown):
                lines.append(f"-# ...dan {len(changes) - len(shown)} lainnya")
        for r in errors[:5]:
            lines.append(f"\u25b8 <@{r.user_id}>: {r.error}")
        if not apply and changes:
            lines.append("\n**Ini simulasi.** Role belum berubah. Jalankan `/tier sync apply:True` untuk menerapkan.")
        await interaction.followup.send(
            embed=embeds.info_embed("Sinkronisasi Tier" + (" (diterapkan)" if apply else " (simulasi)"), "\n".join(lines)[:4000]),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )

    @tier_group.command(name="auto", description="Nyalakan/matikan pemberian tier otomatis (default MATI).")
    @app_commands.describe(aktif="True = role tier dikasih otomatis tiap order selesai + sinkronisasi berkala")
    @staff_only()
    async def auto(self, interaction: discord.Interaction, aktif: bool) -> None:
        await settings_q.set_setting(self.bot.db, tiers_util.AUTO_SETTING_KEY, "1" if aktif else "0")
        text = (
            "Otomatisasi tier **AKTIF**: role dikasih otomatis setiap order selesai dan dicek ulang tiap "
            f"{AUTO_SYNC_HOURS} jam."
            if aktif else "Otomatisasi tier **MATI**. Tidak ada role yang diubah otomatis."
        )
        await interaction.response.send_message(embed=embeds.success_embed(text), ephemeral=True)

    # -- Untuk pembeli ----------------------------------------------------------------------

    @tier_group.command(name="me", description="Lihat tier dan progres belanjamu.")
    async def me(self, interaction: discord.Interaction) -> None:
        db, guild = self.bot.db, interaction.guild
        tiers = await tiers_util.load_tiers(db, guild.id)
        if not tiers:
            await interaction.response.send_message(
                embed=embeds.info_embed("Tier Loyalitas", "Tier loyalitas belum tersedia di server ini."), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        stats = await buyer_stats_q.get_buyer_stats(db, interaction.user.id)
        spend = stats["total_spent"]
        current = tiers_util.pick_tier(tiers, spend)
        upcoming = tiers_util.next_tier_after(tiers, spend)
        progress = 0.0
        if upcoming:
            floor = current.min_spend if current else 0.0
            span = upcoming.min_spend - floor
            progress = (spend - floor) / span if span > 0 else 0.0
        card = {
            "display_name": interaction.user.display_name, "avatar_url": interaction.user.display_avatar.url,
            "spend": spend, "currency": stats["currency_label"] or await self._currency(),
            "current": {"name": current.name, "description": current.description} if current else None,
            "next": {"name": upcoming.name, "remaining": upcoming.min_spend - spend, "progress": progress} if upcoming else None,
            "tiers": [
                {"name": t.name, "min_spend": t.min_spend, "reached": spend >= t.min_spend, "current": bool(current and t.id == current.id)}
                for t in tiers
            ],
        }
        await interaction.followup.send(
            view=components.NoctraLayout(components.tier_progress_container(card), timeout=None), ephemeral=True
        )

    @tier_group.command(name="panel", description="Posting panel publik daftar tier di channel ini.")
    @staff_only()
    async def panel(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(embed=embeds.error_embed("Panel cuma bisa diposting di channel teks."), ephemeral=True)
            return
        db = self.bot.db
        await interaction.response.defer(ephemeral=True)
        old_channel_id = await settings_q.get_setting(db, PANEL_CHANNEL_KEY)
        old_message_id = await settings_q.get_setting(db, PANEL_MESSAGE_KEY)
        message = await interaction.channel.send(view=await self._panel_view(interaction.guild), allowed_mentions=discord.AllowedMentions.none())
        await settings_q.set_setting(db, PANEL_CHANNEL_KEY, str(interaction.channel.id))
        await settings_q.set_setting(db, PANEL_MESSAGE_KEY, str(message.id))
        if old_channel_id and old_message_id:
            try:
                old_channel = self.bot.get_channel(int(old_channel_id))
                if isinstance(old_channel, discord.TextChannel):
                    await (await old_channel.fetch_message(int(old_message_id))).delete()
            except discord.HTTPException:
                pass
        await interaction.followup.send(embed=embeds.success_embed("Panel tier diposting dan akan ter-update otomatis."), ephemeral=True)

    # -- Sinkronisasi berkala ---------------------------------------------------------------------

    @tasks.loop(hours=AUTO_SYNC_HOURS)
    async def auto_sync_loop(self) -> None:
        """Catch-up berkala (misal order yang di-complete lewat jalur lain
        atau role yang sempat gagal). Diam-diam: tanpa DM, cuma ngikutin
        aturan yang sama dengan /tier sync apply:True."""
        try:
            if not await tiers_util.is_auto_enabled(self.bot.db):
                return
            for guild_id in await tiers_q.list_guilds_with_tiers(self.bot.db):
                guild = self.bot.get_guild(guild_id)
                if guild is not None:
                    results = await tiers_util.sync_all(self.bot, guild, apply=True)
                    logger.info("Sinkronisasi tier berkala: %d dicek, %d berubah.", len(results), sum(1 for r in results if r.applied))
        except Exception:  # noqa: BLE001
            logger.exception("Error di sinkronisasi tier berkala.")

    @auto_sync_loop.before_loop
    async def _before_auto_sync(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TiersCog(bot))
