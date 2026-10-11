"""
Command staff: /blacklist (blokir pembeli dari order) dan /buyerinfo
(profil & status kepercayaan pembeli).

  /blacklist add alasan:<> [user:<> | user_id:<>]  -- blokir. user_id buat
        yang udah keluar dari server (picker Discord cuma nampilin member).
  /blacklist remove [user | user_id]   -- buka blokir.
  /blacklist check  [user | user_id]   -- cek status + alasannya.
  /blacklist list                      -- daftar (ephemeral, per halaman).
  /blacklist panel                     -- posting PANEL live di channel ini
        (otomatis ke-update tiap ada perubahan; panel lama diganti).
  /buyerinfo user:<>                   -- status kepercayaan, riwayat order,
        tier, dan info blacklist -- cuma buat staff.

Yang diblokir gak bisa mulai order (guard di bot.ui.views.proceed_to_fields
-> bot.utils.blacklist.guard_order) dan staff dikabarin di channel order-log
kalau dia nyoba. Alasan blacklist TIDAK PERNAH ditunjukin ke yang diblokir.
"""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.core.logger import logger
from bot.database.queries import blacklist as blacklist_q
from bot.database.queries import buyer_stats as buyer_stats_q
from bot.ui import components, embeds
from bot.ui.blacklist_view import _to_unix, build_panel_view, post_panel, refresh_panel
from bot.utils import activity_log, tiers as tiers_util
from bot.utils.blacklist import buyer_standing
from bot.utils.permissions import is_staff, staff_only

REASON_MAX_LENGTH = 200


def _resolve_target(user: discord.User | discord.Member | None, user_id: str | None) -> tuple[int | None, str | None]:
    """Tepat SATU dari `user` / `user_id` harus diisi. Return (id, pesan_error)."""
    if user is not None and user_id:
        return None, "Isi salah satu aja: `user` ATAU `user_id`, jangan dua-duanya."
    if user is not None:
        return user.id, None
    if user_id:
        cleaned = user_id.strip().strip("<@!>")
        if cleaned.isdigit() and 15 <= len(cleaned) <= 21:
            return int(cleaned), None
        return None, "`user_id` harus berupa ID Discord (angka 17-20 digit)."
    return None, "Isi `user` atau `user_id`."


def _format_dt(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).strftime("%d %b %Y") if dt else "?"


class BlacklistCog(commands.Cog):
    blacklist_group = app_commands.Group(
        name="blacklist", description="Blokir pembeli bermasalah dari order.", guild_only=True
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        await blacklist_q.ensure_tables(self.bot.db)

    async def _audit(self, actor: discord.abc.User, title: str, text: str) -> None:
        try:
            await activity_log.log_activity(self.bot, actor, title, text)
        except Exception:  # noqa: BLE001
            logger.warning("Gagal nulis activity log blacklist.")

    # -- /blacklist add / remove / check ---------------------------------------

    @blacklist_group.command(name="add", description="Blokir pembeli dari order (alasan hanya dilihat staff).")
    @app_commands.describe(
        alasan="Alasan blacklist (internal, tidak ditunjukkan ke yang diblokir)",
        user="User yang diblokir (pilih dari server)",
        user_id="ATAU ID Discord-nya (buat yang sudah keluar dari server)",
    )
    @staff_only()
    async def add(
        self, interaction: discord.Interaction, alasan: app_commands.Range[str, 3, REASON_MAX_LENGTH],
        user: discord.User | None = None, user_id: str | None = None,
    ) -> None:
        target_id, error = _resolve_target(user, user_id)
        if error:
            await interaction.response.send_message(embed=embeds.error_embed(error), ephemeral=True)
            return
        if target_id == interaction.user.id:
            await interaction.response.send_message(
                embed=embeds.error_embed("Kamu gak bisa nge-blacklist diri sendiri."), ephemeral=True
            )
            return
        if self.bot.user and target_id == self.bot.user.id:
            await interaction.response.send_message(embed=embeds.error_embed("Itu bot ini sendiri."), ephemeral=True)
            return
        if user is not None and user.bot:
            await interaction.response.send_message(embed=embeds.error_embed("Akun bot gak perlu di-blacklist."), ephemeral=True)
            return
        member = interaction.guild.get_member(target_id) if interaction.guild else None
        if member is not None and (member.guild_permissions.administrator):
            await interaction.response.send_message(
                embed=embeds.error_embed("User ini Administrator server, gak bisa di-blacklist."), ephemeral=True
            )
            return

        is_new = await blacklist_q.add_entry(self.bot.db, target_id, alasan, interaction.user.id)
        await refresh_panel(self.bot)
        await self._audit(interaction.user, "Blacklist Ditambah", f"<@{target_id}> (`{target_id}`) -- {alasan}")
        text = (
            f"<@{target_id}> masuk blacklist dan tidak bisa mulai order lagi."
            if is_new else f"<@{target_id}> sudah ada di blacklist -- alasannya diperbarui."
        )
        await interaction.response.send_message(
            embed=embeds.success_embed(text), ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @blacklist_group.command(name="remove", description="Buka blokir pembeli.")
    @app_commands.describe(user="User yang dibuka blokirnya", user_id="ATAU ID Discord-nya")
    @staff_only()
    async def remove(
        self, interaction: discord.Interaction, user: discord.User | None = None, user_id: str | None = None
    ) -> None:
        target_id, error = _resolve_target(user, user_id)
        if error:
            await interaction.response.send_message(embed=embeds.error_embed(error), ephemeral=True)
            return
        removed = await blacklist_q.remove_entry(self.bot.db, target_id)
        if not removed:
            await interaction.response.send_message(
                embed=embeds.error_embed("User itu memang tidak ada di blacklist."), ephemeral=True
            )
            return
        await refresh_panel(self.bot)
        await self._audit(interaction.user, "Blacklist Dicabut", f"<@{target_id}> (`{target_id}`) dibuka blokirnya.")
        await interaction.response.send_message(
            embed=embeds.success_embed(f"<@{target_id}> dibuka blokirnya dan bisa order lagi."),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )

    @blacklist_group.command(name="check", description="Cek apakah user ada di blacklist.")
    @app_commands.describe(user="User yang dicek", user_id="ATAU ID Discord-nya")
    @staff_only()
    async def check(
        self, interaction: discord.Interaction, user: discord.User | None = None, user_id: str | None = None
    ) -> None:
        target_id, error = _resolve_target(user, user_id)
        if error:
            await interaction.response.send_message(embed=embeds.error_embed(error), ephemeral=True)
            return
        entry = await blacklist_q.get_entry(self.bot.db, target_id)
        if entry is None:
            await interaction.response.send_message(
                embed=embeds.success_embed(f"<@{target_id}> **tidak** ada di blacklist."),
                ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        await interaction.response.send_message(
            embed=embeds.error_embed(
                f"<@{target_id}> **ada di blacklist**.\n\u25b8 Alasan: {discord.utils.escape_markdown(entry['reason'])}\n"
                f"\u25b8 Oleh: <@{entry['added_by']}>  \u00b7  <t:{_to_unix(entry['created_at'])}:R>"
            ),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )

    # -- /blacklist list / panel ------------------------------------------------

    @blacklist_group.command(name="list", description="Lihat daftar blacklist (per halaman).")
    @staff_only()
    async def list_entries(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            view=await build_panel_view(self.bot.db, 0), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @blacklist_group.command(name="panel", description="Posting panel blacklist LIVE di channel ini (otomatis ke-update).")
    @staff_only()
    async def panel(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=embeds.error_embed("Panel cuma bisa diposting di channel teks."), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await post_panel(self.bot, interaction.channel)
        except discord.HTTPException:
            logger.exception("Gagal posting panel blacklist.")
            await interaction.followup.send(embed=embeds.error_embed("Gagal posting panel di channel ini."), ephemeral=True)
            return
        await interaction.followup.send(
            embed=embeds.success_embed("Panel blacklist diposting dan akan otomatis ter-update tiap ada perubahan."),
            ephemeral=True,
        )

    # -- /buyerinfo ---------------------------------------------------------------

    @app_commands.command(name="buyerinfo", description="Profil pembeli: status kepercayaan, riwayat order, tier (staff).")
    @app_commands.describe(user="Pembeli yang dicek")
    @app_commands.guild_only()
    @staff_only()
    async def buyerinfo(self, interaction: discord.Interaction, user: discord.User) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        db = self.bot.db
        stats = await buyer_stats_q.get_buyer_stats(db, user.id)
        entry = await blacklist_q.get_entry(db, user.id)
        standing = buyer_standing(stats, entry is not None)

        guild = interaction.guild
        tier_info: dict = {}
        if guild is not None:
            tiers = await tiers_util.load_tiers(db, guild.id)
            current = tiers_util.pick_tier(tiers, stats["total_spent"])
            upcoming = tiers_util.next_tier_after(tiers, stats["total_spent"])
            tier_info = {
                "current": current.name if current else None,
                "next": {"name": upcoming.name, "remaining": upcoming.min_spend - stats["total_spent"]} if upcoming else None,
            }
        member = guild.get_member(user.id) if guild else None
        card = {
            "user": {
                "id": user.id, "name": user.name, "display_name": user.display_name,
                "avatar_url": user.display_avatar.url,
                "created_display": _format_dt(user.created_at),
                "joined_display": _format_dt(member.joined_at) if member else None,
            },
            "standing": standing,
            "blacklist": (
                {"reason": entry["reason"], "added_by": entry["added_by"], "created_ts": _to_unix(entry["created_at"])}
                if entry else None
            ),
            "stats": stats,
            "tier": tier_info,
            "currency": stats["currency_label"] or await buyer_stats_q.get_store_currency(db),
        }
        container = components.buyer_info_container(card)
        await interaction.followup.send(
            view=components.NoctraLayout(container, timeout=None), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BlacklistCog(bot))
