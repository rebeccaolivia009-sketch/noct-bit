"""
Sembunyiin command staff/owner dari member biasa di daftar command Discord.

CARA KERJANYA
  Discord cuma punya SATU mekanisme buat nyembunyiin command: field
  `default_member_permissions`. Member yang gak punya permission itu gak
  bakal ngeliat command-nya di daftar (dan gak bisa ngejalanin). Pemilik
  server dan admin selalu ngeliat semuanya.

  Daripada ngedit setiap cog satu-satu, modul ini jalan SEKALI pas startup
  (setelah semua cog ke-load, sebelum sync): tiap command yang dijaga
  `staff_only()` (lihat bot.utils.permissions) otomatis dikasih
  default_member_permissions. Command customer (/shop, /buy, /stats, dst)
  gak disentuh, jadi tetep keliatan buat semua.

PENTING
  * Ini cuma soal TAMPILAN. Pengecekan staff_only() pas command dijalanin
    tetep ada dan itu yang bener-bener ngejaga.
  * Visibilitas berbasis PERMISSION, bukan role. Staff yang rolenya gak
    punya permission itu gak bakal ngeliat commandnya -- kasih permission-nya
    ke role staff, atau ganti STAFF_COMMAND_PERMISSION, atau tambahin
    override role di Server Settings -> Integrations (bot gak bisa ngatur
    override itu sendiri).
  * Discord cuma bisa nyembunyiin di level command TERATAS. Grup yang
    isinya campur (ada subcommand staff DAN customer) gak bisa disembunyiin
    sebagian -- grup kayak gitu dibiarin keliatan dan dicatat di log
    startup. Pecah jadi dua command kalau mau disembunyiin.

ENV (opsional, di Railway)
  STAFF_COMMAND_VISIBILITY   on (default) | off -> matiin fitur ini
  STAFF_COMMAND_PERMISSION   nama permission discord.py (default
                             manage_messages), misal manage_guild,
                             administrator, moderate_members
"""

from __future__ import annotations

import inspect
import os
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

import discord
from discord import app_commands

from bot.core.logger import logger

STAFF_CHECK_MODULE = "bot.utils.permissions"
DEFAULT_PERMISSION = "manage_messages"
ENV_ENABLED = "STAFF_COMMAND_VISIBILITY"
ENV_PERMISSION = "STAFF_COMMAND_PERMISSION"


@dataclass
class VisibilityReport:
    hidden: list[str] = field(default_factory=list)    # command teratas yang disembunyiin
    public: list[str] = field(default_factory=list)    # command customer (gak disentuh)
    mixed: list[tuple[str, list[str], list[str]]] = field(default_factory=list)  # (grup, subcmd staff, subcmd publik)
    preset: list[str] = field(default_factory=list)    # udah punya default_permissions sendiri


def is_staff_check(check) -> bool:
    """True kalau `check` itu pengecekan staff/owner dari bot.utils.permissions."""
    try:
        target = inspect.unwrap(check)
    except ValueError:
        target = check
    return getattr(target, "__module__", None) == STAFF_CHECK_MODULE


def _leaves(command) -> Iterator[tuple[str, bool]]:
    """(nama lengkap, dijaga staff?) buat tiap command di bawah `command`."""
    if isinstance(command, app_commands.Group):
        for child in command.commands:
            yield from _leaves(child)
    else:
        yield command.qualified_name, any(is_staff_check(c) for c in command.checks)


def apply_staff_visibility(tree: app_commands.CommandTree, permission: discord.Permissions) -> VisibilityReport:
    """Pasang `permission` sebagai default_member_permissions di semua
    command teratas yang SELURUH isinya dijaga staff_only()."""
    report = VisibilityReport()
    for top in tree.get_commands():
        leaves = list(_leaves(top))
        staff = [name for name, is_staff in leaves if is_staff]
        public = [name for name, is_staff in leaves if not is_staff]
        if not staff:
            report.public.append(top.name)
        elif public:
            report.mixed.append((top.name, staff, public))
        elif top.default_permissions is not None:
            report.preset.append(top.name)   # dev udah nentuin sendiri -- jangan ditimpa
        else:
            top.default_permissions = permission
            report.hidden.append(top.name)
    return report


def resolve_config(environ: Mapping[str, str]) -> tuple[discord.Permissions | None, str, str | None]:
    """(permission atau None kalau dimatiin, nama permission, peringatan)."""
    if environ.get(ENV_ENABLED, "on").strip().lower() in ("off", "0", "false", "no"):
        return None, "", None
    name = environ.get(ENV_PERMISSION, DEFAULT_PERMISSION).strip().lower() or DEFAULT_PERMISSION
    warning = None
    if name not in discord.Permissions.VALID_FLAGS:
        warning = f"{ENV_PERMISSION}='{name}' bukan permission yang valid, pake '{DEFAULT_PERMISSION}'."
        name = DEFAULT_PERMISSION
    return discord.Permissions(**{name: True}), name, warning


def apply_from_env(tree: app_commands.CommandTree, environ: Mapping[str, str] | None = None) -> VisibilityReport | None:
    """Dipanggil NoctraBot.setup_hook: baca env, terapin, dan catat hasilnya di log."""
    permission, name, warning = resolve_config(os.environ if environ is None else environ)
    if warning:
        logger.warning(warning)
    if permission is None:
        logger.info("Visibilitas command staff: DIMATIKAN (%s=off).", ENV_ENABLED)
        return None
    report = apply_staff_visibility(tree, permission)
    logger.info(
        "Command staff disembunyiin dari member tanpa permission '%s' (%d): %s",
        name, len(report.hidden), ", ".join(sorted(f"/{n}" for n in report.hidden)) or "-",
    )
    for group, staff, public in report.mixed:
        logger.warning(
            "Grup /%s CAMPUR (staff: %s | publik: %s) -- gak bisa disembunyiin sebagian, dibiarin keliatan. "
            "Pecah jadi dua command kalau mau disembunyiin.",
            group, ", ".join(staff), ", ".join(public),
        )
    return report
