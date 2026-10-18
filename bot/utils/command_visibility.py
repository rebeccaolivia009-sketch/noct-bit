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
  * Visibilitas berbasis PERMISSION, bukan role, padahal "staff" di bot ini
    = Administrator ATAU pemegang role yang diatur lewat /settings. Jadi
    command staff disembunyiin di balik satu permission PENANDA yang gak
    berbahaya (default `view_creator_monetization_analytics`, cuma ngatur
    akses analitik monetisasi kreator -- gak ngapa-ngapain di server biasa),
    dan role staff dikasih permission itu lewat `/staffperm sync` (lihat
    bot.cogs.staff_access). Member biasa gak punya -> gak ngeliat command staff.
  * Command yang dijaga admin_only() (khusus Administrator) disembunyiin
    di balik permission Administrator -- role staff pun gak ngeliatnya.
  * Discord cuma bisa nyembunyiin di level command TERATAS. Grup yang
    isinya campur (ada subcommand staff DAN customer) gak bisa disembunyiin
    sebagian -- grup kayak gitu dibiarin keliatan dan dicatat di log
    startup. Pecah jadi dua command kalau mau disembunyiin.

ENV (opsional, di Railway)
  STAFF_COMMAND_VISIBILITY   on (default) | off -> matiin fitur ini
  STAFF_COMMAND_PERMISSION   nama permission discord.py. Default: permission
                             penanda di atas. Isi permission lain (misal
                             manage_messages) kalau role staff lu emang
                             udah punya -- `/staffperm sync` gak dibutuhin
                             lagi, tapi pastiin role staff punya permission itu.
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
MARKER_PERMISSION = "view_creator_monetization_analytics"
FALLBACK_PERMISSION = "manage_messages"   # kalau discord.py lawas gak kenal permission penanda
DEFAULT_PERMISSION = MARKER_PERMISSION if MARKER_PERMISSION in discord.Permissions.VALID_FLAGS else FALLBACK_PERMISSION
ENV_ENABLED = "STAFF_COMMAND_VISIBILITY"
ENV_PERMISSION = "STAFF_COMMAND_PERMISSION"


@dataclass
class VisibilityReport:
    hidden: list[str] = field(default_factory=list)    # command teratas yang disembunyiin (semua)
    admin_hidden: list[str] = field(default_factory=list)  # sebagian dari `hidden`: cuma buat Administrator
    public: list[str] = field(default_factory=list)    # command customer (gak disentuh)
    mixed: list[tuple[str, list[str], list[str]]] = field(default_factory=list)  # (grup, subcmd staff, subcmd publik)
    preset: list[str] = field(default_factory=list)    # udah punya default_permissions sendiri


def check_kind(check) -> str | None:
    """"admin" (admin_only), "staff" (staff_only), atau None kalau bukan
    pengecekan dari bot.utils.permissions."""
    try:
        target = inspect.unwrap(check)
    except ValueError:
        target = check
    if getattr(target, "__module__", None) != STAFF_CHECK_MODULE:
        return None
    return "admin" if "admin_only" in getattr(target, "__qualname__", "") else "staff"


def is_staff_check(check) -> bool:
    """True kalau `check` itu pengecekan staff/admin dari bot.utils.permissions."""
    return check_kind(check) is not None


def _leaves(command) -> Iterator[tuple[str, str | None]]:
    """(nama lengkap, jenis penjaga) buat tiap command di bawah `command`.
    Kalau satu command punya dua penjaga, yang paling ketat (admin) menang."""
    if isinstance(command, app_commands.Group):
        for child in command.commands:
            yield from _leaves(child)
    else:
        kinds = {check_kind(c) for c in command.checks} - {None}
        yield command.qualified_name, ("admin" if "admin" in kinds else "staff" if kinds else None)


def apply_staff_visibility(
    tree: app_commands.CommandTree,
    permission: discord.Permissions,
    admin_permission: discord.Permissions | None = None,
) -> VisibilityReport:
    """Pasang default_member_permissions di semua command teratas yang
    SELURUH isinya dijaga: `permission` buat staff_only(), dan
    `admin_permission` (default Administrator) kalau semuanya admin_only().
    Grup yang campur staff_only + admin_only pake `permission` (staff) --
    subcommand admin-nya tetep ditolak pas dijalanin."""
    admin_permission = admin_permission or discord.Permissions(administrator=True)
    report = VisibilityReport()
    for top in tree.get_commands():
        leaves = list(_leaves(top))
        guarded = [(name, kind) for name, kind in leaves if kind]
        public = [name for name, kind in leaves if not kind]
        if not guarded:
            report.public.append(top.name)
        elif public:
            report.mixed.append((top.name, [n for n, _ in guarded], public))
        elif top.default_permissions is not None:
            report.preset.append(top.name)   # dev udah nentuin sendiri -- jangan ditimpa
        else:
            admin_only = all(kind == "admin" for _, kind in guarded)
            top.default_permissions = admin_permission if admin_only else permission
            report.hidden.append(top.name)
            if admin_only:
                report.admin_hidden.append(top.name)
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
        "Command staff disembunyiin dari member tanpa permission '%s' (%d, %d di antaranya khusus Administrator): %s",
        name, len(report.hidden), len(report.admin_hidden),
        ", ".join(sorted(f"/{n}" for n in report.hidden)) or "-",
    )
    for group, staff, public in report.mixed:
        logger.warning(
            "Grup /%s CAMPUR (staff: %s | publik: %s) -- gak bisa disembunyiin sebagian, dibiarin keliatan. "
            "Pecah jadi dua command kalau mau disembunyiin.",
            group, ", ".join(staff), ", ".join(public),
        )
    return report
