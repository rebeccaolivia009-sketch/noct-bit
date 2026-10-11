"""
Tier loyalitas NOCTRA (mis. Royal Buyer, King of Noctra): tiap tier =
nama + role Discord + minimal total belanja. Role dikasih OTOMATIS begitu
total belanja (order completed+paid, aturan sama kayak leaderboard)
nyampe ambangnya -- gak perlu dikasih manual lagi.

ATURAN (sengaja konservatif):
  * HANYA NAIK -- bot gak pernah nyabut role tier karena belanja turun
    (misal ada refund) dan gak pernah nurunin role yang staff kasih manual
    lebih tinggi dari hasil hitungan.
  * Naik tier = role tier BARU ditambah, role tier yang LEBIH RENDAH yang
    dipegang user dicabut (biar user cuma pegang satu tier tertinggi).
  * Akun yang dikecualiin dari leaderboard (/settings leaderboard_exclude,
    biasanya staff/tester) gak pernah diutak-atik.
  * Otomatisasinya MATI secara default (`/tier auto`) -- jalanin
    `/tier sync` (simulasi dulu) buat ngecek hasilnya sebelum dinyalain,
    biar gak ada role yang berubah mendadak di hari pertama.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import discord

from bot.core.logger import logger
from bot.database.queries import buyer_stats as buyer_stats_q
from bot.database.queries import loyalty_tiers as tiers_q
from bot.database.queries import settings as settings_q
from bot.ui import components, embeds
from bot.utils.helpers import RuntimeSettings

AUTO_SETTING_KEY = "tier_auto_enabled"
AUDIT_REASON = "Tier loyalitas NOCTRA (otomatis)"
ROLE_CALL_DELAY_SECONDS = 0.4  # jeda antar panggilan role biar aman dari rate limit


@dataclass(frozen=True, slots=True)
class Tier:
    id: int
    guild_id: int
    name: str
    role_id: int
    min_spend: float
    description: str | None


def tiers_from_rows(rows) -> list[Tier]:
    return [
        Tier(int(r["id"]), int(r["guild_id"]), r["name"], int(r["role_id"]), float(r["min_spend"]), r["description"])
        for r in rows
    ]


def pick_tier(tiers: list[Tier], spend: float) -> Tier | None:
    """Tier TERTINGGI yang ambangnya udah kecapai."""
    reached = [t for t in tiers if t.min_spend <= spend]
    return max(reached, key=lambda t: t.min_spend) if reached else None


def next_tier_after(tiers: list[Tier], spend: float) -> Tier | None:
    """Tier terdekat yang BELUM kecapai."""
    upcoming = [t for t in tiers if t.min_spend > spend]
    return min(upcoming, key=lambda t: t.min_spend) if upcoming else None


@dataclass(slots=True)
class TierPlan:
    target: Tier | None            # tier yang BERHAK dipegang user (dari belanja)
    held_highest: Tier | None      # tier tertinggi yang SEKARANG dipegang user
    add_role_id: int | None = None
    remove_role_ids: list[int] = field(default_factory=list)
    kept_manual_higher: bool = False  # user udah pegang tier lebih tinggi (manual) -- gak diutak-atik

    @property
    def has_changes(self) -> bool:
        return self.add_role_id is not None or bool(self.remove_role_ids)

    @property
    def is_promotion(self) -> bool:
        return self.add_role_id is not None and self.target is not None


def plan_member(tiers: list[Tier], member_role_ids: set[int], spend: float) -> TierPlan:
    """Fungsi murni: tentuin role apa yang harus ditambah/dicabut. Gak
    nyentuh Discord sama sekali -- makanya gampang dites per skenario."""
    held = [t for t in tiers if t.role_id in member_role_ids]
    held_highest = max(held, key=lambda t: t.min_spend) if held else None
    target = pick_tier(tiers, spend)
    plan = TierPlan(target=target, held_highest=held_highest)
    if target is None:
        return plan
    if held_highest is not None and held_highest.min_spend > target.min_spend:
        plan.kept_manual_higher = True
        return plan
    if target.role_id not in member_role_ids:
        plan.add_role_id = target.role_id
    plan.remove_role_ids = [
        t.role_id for t in held if t.min_spend < target.min_spend and t.role_id != target.role_id
    ]
    return plan


def role_problem(guild: discord.Guild, role: discord.Role | None) -> str | None:
    """Alasan kenapa bot GAK BISA ngasih/nyabut `role` (None = aman)."""
    if role is None:
        return "role-nya gak ketemu (mungkin udah dihapus)"
    if role.is_default():
        return "@everyone gak bisa dipake sebagai role tier"
    if role.managed:
        return "role ini dikelola integrasi/bot lain"
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return "bot gak punya izin Manage Roles"
    if role >= me.top_role:
        return "posisi role ini lebih tinggi/sama dengan role bot (naikin role bot di atas role tier)"
    return None


@dataclass(slots=True)
class SyncResult:
    user_id: int
    spend: float = 0.0
    plan: TierPlan | None = None
    applied: bool = False
    error: str | None = None
    skipped: str | None = None

    @property
    def promoted(self) -> bool:
        return self.applied and self.plan is not None and self.plan.is_promotion


async def is_auto_enabled(db) -> bool:
    return (await settings_q.get_setting(db, AUTO_SETTING_KEY)) == "1"


async def load_tiers(db, guild_id: int) -> list[Tier]:
    return tiers_from_rows(await tiers_q.list_tiers(db, guild_id))


async def tier_name_for_spend(db, guild_id: int, spend: float) -> str | None:
    """Nama tier berdasarkan belanja (bukan role yang dipegang) -- dipake
    kartu pengumuman pembelian."""
    return (lambda t: t.name if t else None)(pick_tier(await load_tiers(db, guild_id), spend))


async def sync_user(
    bot, guild: discord.Guild, user_id: int, *, apply: bool,
    tiers: list[Tier] | None = None, excluded: list[int] | None = None,
) -> SyncResult:
    """Hitung (dan kalau apply=True, TERAPIN) tier satu user. Gak pernah
    raise: semua masalah dikembaliin di SyncResult.error / .skipped."""
    result = SyncResult(user_id=user_id)
    db = bot.db
    try:
        tiers = tiers if tiers is not None else await load_tiers(db, guild.id)
        if not tiers:
            result.skipped = "belum ada tier yang diatur"
            return result
        excluded = excluded if excluded is not None else await RuntimeSettings(db).leaderboard_excluded_user_ids()
        if user_id in excluded:
            result.skipped = "akun dikecualiin dari leaderboard"
            return result

        result.spend = (await buyer_stats_q.get_buyer_stats(db, user_id))["total_spent"]
        if result.spend <= 0:
            result.skipped = "belum ada belanja"
            return result

        member = guild.get_member(user_id)
        if member is None:
            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                result.skipped = "user gak ada di server"
                return result

        plan = plan_member(tiers, {r.id for r in member.roles}, result.spend)
        result.plan = plan
        if not plan.has_changes or not apply:
            return result

        add_role = guild.get_role(plan.add_role_id) if plan.add_role_id else None
        remove_roles = [guild.get_role(rid) for rid in plan.remove_role_ids]
        for role in ([add_role] if plan.add_role_id else []) + remove_roles:
            problem = role_problem(guild, role)
            if problem:
                result.error = problem
                return result

        if add_role:
            await member.add_roles(add_role, reason=AUDIT_REASON)
        for role in remove_roles:
            if role is not None:
                await member.remove_roles(role, reason=AUDIT_REASON)
        result.applied = True
        return result
    except discord.Forbidden:
        result.error = "Discord nolak (izin bot kurang atau posisi role bot terlalu rendah)"
    except discord.HTTPException as exc:
        result.error = f"Discord error: {exc}"
    except Exception:  # noqa: BLE001
        logger.exception("Sync tier gagal buat user %s.", user_id)
        result.error = "error tak terduga (lihat log)"
    return result


async def handle_order_completed(bot, order) -> None:
    """Dipanggil bot.utils.order_actions.mark_completed begitu order kelar:
    kalau otomatisasi tier nyala, update tier si pembeli dan kabarin dia
    kalau naik tier. Best-effort -- gak boleh ngeganggu alur order."""
    db = bot.db
    if not await is_auto_enabled(db):
        return
    for guild_id in await tiers_q.list_guilds_with_tiers(db):
        guild = bot.get_guild(guild_id)
        if guild is None or guild.get_member(order["user_id"]) is None:
            continue
        result = await sync_user(bot, guild, order["user_id"], apply=True)
        if result.error:
            logger.warning("Auto-tier gagal buat user %s: %s", order["user_id"], result.error)
        if result.promoted:
            await announce_promotion(bot, guild, result)


async def announce_promotion(bot, guild: discord.Guild, result: SyncResult) -> None:
    """DM kartu "Naik Tier" ke pembeli + catatan singkat ke order-log."""
    db = bot.db
    tiers = await load_tiers(db, guild.id)
    plan = result.plan
    assert plan is not None and plan.target is not None
    upcoming = next_tier_after(tiers, result.spend)
    currency = await buyer_stats_q.get_store_currency(db)
    card = {
        "name": plan.target.name, "description": plan.target.description, "spend": result.spend,
        "next_name": upcoming.name if upcoming else None,
        "next_remaining": (upcoming.min_spend - result.spend) if upcoming else None,
        "currency": currency,
    }
    user = bot.get_user(result.user_id)
    if user is None:
        try:
            user = await bot.fetch_user(result.user_id)
        except discord.HTTPException:
            user = None
    if user is not None:
        try:
            await user.send(view=components.NoctraLayout(components.tier_up_container(card), timeout=None))
        except discord.HTTPException:
            pass  # DM ditutup -- gak masalah, role tetep udah masuk

    try:
        log_channel_id = await RuntimeSettings(db).order_log_channel_id()
        channel = bot.get_channel(log_channel_id) if log_channel_id else None
        if isinstance(channel, discord.TextChannel):
            await channel.send(
                embed=embeds.info_embed("Naik Tier", f"<@{result.user_id}> naik ke tier **{plan.target.name}**."),
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except discord.HTTPException:
        pass


async def sync_all(bot, guild: discord.Guild, *, apply: bool) -> list[SyncResult]:
    """Sinkronisasi semua pembeli yang punya belanja. Sengaja sekuensial
    + jeda kecil antar user yang beneran ganti role (rate limit Discord)."""
    db = bot.db
    tiers = await load_tiers(db, guild.id)
    if not tiers:
        return []
    excluded = await RuntimeSettings(db).leaderboard_excluded_user_ids()
    results: list[SyncResult] = []
    for user_id, _spend in await buyer_stats_q.get_all_spenders(db, excluded):
        result = await sync_user(bot, guild, user_id, apply=apply, tiers=tiers, excluded=excluded)
        results.append(result)
        if apply and result.applied:
            await asyncio.sleep(ROLE_CALL_DELAY_SECONDS)
    return results
