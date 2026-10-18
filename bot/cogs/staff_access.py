"""
Command admin: /staffperm -- atur siapa yang bisa MELIHAT command staff.

Latar belakang (lihat bot.utils.command_visibility): command staff
disembunyiin dari member biasa lewat satu permission PENANDA. Tapi "staff"
di bot ini = Administrator ATAU pemegang role dari /settings, dan role itu
belum tentu punya permission penanda tadi. /staffperm sync ngasih
permission itu ke role staff (satu klik, bukan override per-command di
Server Settings -> Integrations), /staffperm revoke nyabutnya lagi.

Semua subcommand-nya admin_only() -- sengaja lebih ketat dari staff_only(),
karena ini ngubah permission role.
"""

from __future__ import annotations

import os

import discord
from discord import app_commands
from discord.ext import commands

from bot.core.logger import logger
from bot.ui import embeds
from bot.utils import command_visibility as visibility
from bot.utils.helpers import RuntimeSettings
from bot.utils.permissions import admin_only

AUDIT_REASON = "Visibilitas command staff NOCTRA (/staffperm)"


def role_edit_problem(guild: discord.Guild, role: discord.Role) -> str | None:
    """Kenapa bot gak bisa ngedit permission `role` (None = aman)."""
    if role.is_default():
        return "@everyone gak bisa dipake sebagai role staff."
    if role.managed:
        return "Role ini dikelola integrasi/bot lain, permission-nya gak bisa diubah."
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        return "Bot gak punya izin Manage Roles."
    if role >= me.top_role:
        return "Posisi role staff lebih tinggi/sama dengan role bot -- naikin role bot di atas role staff."
    return None


class StaffAccessCog(commands.Cog):
    """Kelola siapa yang ngeliat command staff."""

    staffperm_group = app_commands.Group(
        name="staffperm",
        description="Atur siapa yang bisa melihat command staff (khusus Administrator).",
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- util -----------------------------------------------------------------------

    @staticmethod
    def _config() -> tuple[discord.Permissions | None, str]:
        permission, name, _ = visibility.resolve_config(os.environ)
        return permission, name

    async def _staff_role(self, guild: discord.Guild) -> tuple[discord.Role | None, str | None]:
        role_id = await RuntimeSettings(self.bot.db).staff_role_id()
        if role_id is None:
            return None, "Role staff belum diatur. Atur dulu lewat `/settings`."
        role = guild.get_role(role_id)
        if role is None:
            return None, "Role staff yang diatur gak ketemu di server ini (mungkin udah dihapus). Atur ulang lewat `/settings`."
        return role, None

    def _only_marker(self, name: str) -> str | None:
        """Command ini cuma ngelola permission PENANDA. Kalau admin milih
        permission lain lewat env, jangan diutak-atik dari sini."""
        if name == visibility.MARKER_PERMISSION:
            return None
        return (
            f"Permission aktif sekarang `{name}` (dari `STAFF_COMMAND_PERMISSION`). Command ini cuma ngelola "
            f"permission penanda `{visibility.MARKER_PERMISSION}` -- pastiin role staff punya `{name}` lewat "
            "pengaturan role di Discord, atau hapus env itu buat balik ke permission penanda."
        )

    # -- command --------------------------------------------------------------------

    @staffperm_group.command(name="status", description="Cek apakah role staff udah bisa melihat command staff.")
    @admin_only()
    async def status(self, interaction: discord.Interaction) -> None:
        permission, name = self._config()
        if permission is None:
            await interaction.response.send_message(
                embed=embeds.info_embed(
                    "Visibilitas Command Staff",
                    "**DIMATIKAN** (`STAFF_COMMAND_VISIBILITY=off`) -- semua command keliatan buat semua orang.",
                ),
                ephemeral=True,
            )
            return
        report = getattr(self.bot, "visibility_report", None)
        lines = [f"Permission penanda: `{name}`"]
        if report is not None:
            staff_count = len(report.hidden) - len(report.admin_hidden)
            lines.append(
                f"Command tersembunyi dari member biasa: **{len(report.hidden)}** "
                f"({staff_count} buat staff, {len(report.admin_hidden)} khusus Administrator)"
            )
            if report.mixed:
                lines.append(
                    "\u26A0\uFE0F Grup campur (gak bisa disembunyiin): "
                    + ", ".join(f"`/{g}`" for g, _, _ in report.mixed)
                )
        role, error = await self._staff_role(interaction.guild)
        if role is None:
            lines.append(f"\u26A0\uFE0F {error}")
        else:
            has = role.permissions >= permission
            lines.append(f"Role staff: {role.mention} \u2014 punya permission penanda: **{'ya' if has else 'TIDAK'}**")
            if not has:
                lines.append("\u27A1\uFE0F Jalanin `/staffperm sync` biar staff bisa melihat command staff.")
            problem = role_edit_problem(interaction.guild, role)
            if problem and not has:
                lines.append(f"\u26A0\uFE0F Bot belum bisa ngedit role itu: {problem}")
        lines.append("-# Administrator selalu melihat semua command, apapun pengaturannya.")
        await interaction.response.send_message(
            embed=embeds.info_embed("Visibilitas Command Staff", "\n".join(lines)), ephemeral=True
        )

    @staffperm_group.command(name="sync", description="Kasih permission penanda ke role staff biar staff bisa melihat command staff.")
    @admin_only()
    async def sync(self, interaction: discord.Interaction) -> None:
        permission, name = self._config()
        if permission is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Visibilitas command staff lagi dimatikan (`STAFF_COMMAND_VISIBILITY=off`)."),
                ephemeral=True,
            )
            return
        refusal = self._only_marker(name)
        if refusal:
            await interaction.response.send_message(embed=embeds.error_embed(refusal), ephemeral=True)
            return
        role, error = await self._staff_role(interaction.guild)
        if role is None:
            await interaction.response.send_message(embed=embeds.error_embed(error), ephemeral=True)
            return
        if role.permissions >= permission:
            await interaction.response.send_message(
                embed=embeds.success_embed(f"{role.mention} udah punya permission penanda. Gak ada yang perlu diubah."),
                ephemeral=True,
            )
            return
        problem = role_edit_problem(interaction.guild, role)
        if problem:
            await interaction.response.send_message(embed=embeds.error_embed(problem), ephemeral=True)
            return
        try:
            await role.edit(permissions=role.permissions | permission, reason=AUDIT_REASON)
        except discord.Forbidden:
            await interaction.response.send_message(
                embed=embeds.error_embed("Discord nolak (izin bot kurang atau posisi role bot terlalu rendah)."), ephemeral=True
            )
            return
        except discord.HTTPException as exc:
            await interaction.response.send_message(embed=embeds.error_embed(f"Discord error: {exc}"), ephemeral=True)
            return
        logger.info("Permission penanda '%s' ditambahin ke role staff %s (%s).", name, role.name, role.id)
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"{role.mention} sekarang punya permission penanda `{name}`. Member berole itu bakal melihat command "
                "staff (kalau belum muncul, tutup-buka aplikasi Discord).\n"
                "-# Permission ini gak ngasih kemampuan apa-apa di server biasa -- cuma buat visibilitas command."
            ),
            ephemeral=True,
        )

    @staffperm_group.command(name="revoke", description="Cabut permission penanda dari role staff (command staff jadi tersembunyi buat mereka).")
    @admin_only()
    async def revoke(self, interaction: discord.Interaction) -> None:
        permission, name = self._config()
        if permission is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Visibilitas command staff lagi dimatikan (`STAFF_COMMAND_VISIBILITY=off`)."),
                ephemeral=True,
            )
            return
        refusal = self._only_marker(name)
        if refusal:
            await interaction.response.send_message(embed=embeds.error_embed(refusal), ephemeral=True)
            return
        role, error = await self._staff_role(interaction.guild)
        if role is None:
            await interaction.response.send_message(embed=embeds.error_embed(error), ephemeral=True)
            return
        if not role.permissions >= permission:
            await interaction.response.send_message(
                embed=embeds.success_embed(f"{role.mention} emang gak punya permission penanda. Gak ada yang perlu dicabut."),
                ephemeral=True,
            )
            return
        problem = role_edit_problem(interaction.guild, role)
        if problem:
            await interaction.response.send_message(embed=embeds.error_embed(problem), ephemeral=True)
            return
        try:
            await role.edit(permissions=discord.Permissions(role.permissions.value & ~permission.value), reason=AUDIT_REASON)
        except discord.HTTPException as exc:
            await interaction.response.send_message(embed=embeds.error_embed(f"Gagal nyabut: {exc}"), ephemeral=True)
            return
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Permission penanda dicabut dari {role.mention}. Staff berole itu gak lagi melihat command staff "
                "(Administrator tetap melihat semuanya)."
            ),
            ephemeral=True,
        )

    # -- pengingat di log --------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Kalau role staff belum punya permission penanda, staff-nya
        bakal kehilangan command -- ingetin di log biar ketauan sebelum ada yang nanya."""
        try:
            permission, name = self._config()
            if permission is None or name != visibility.MARKER_PERMISSION:
                return
            role_id = await RuntimeSettings(self.bot.db).staff_role_id()
            if role_id is None:
                return
            for guild in self.bot.guilds:
                role = guild.get_role(role_id)
                if role is not None and not role.permissions >= permission:
                    logger.warning(
                        "Role staff '%s' di guild %s belum punya permission penanda '%s' -- staff berole itu "
                        "gak bakal melihat command staff. Jalanin /staffperm sync.",
                        role.name, guild.id, name,
                    )
        except Exception:  # noqa: BLE001
            logger.exception("Gagal ngecek permission role staff.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StaffAccessCog(bot))
