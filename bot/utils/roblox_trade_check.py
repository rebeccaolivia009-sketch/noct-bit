"""
Logika checker akun Roblox buat jual-beli limited (/tradecheck): ngumpulin
data dari bot.utils.roblox_api, ngitung total RAP/Value, milih item tumbal,
dan nentuin verdict.

ATURAN VERDICT (sengaja ketat -- ini soal item mahal):

  * LAYAK       cuma kalau ROBLOX sendiri yang bilang akun itu bisa di-trade
                (verifikasi resmi, butuh ROBLOX_COOKIE) DAN inventory publik
                DAN ada item tumbal. Gak pernah "LAYAK" dari tebakan.
  * BELUM LAYAK ada penghalang yang PASTI: akun banned, inventory privat,
                gak punya limited sama sekali (gak ada tumbal), atau Roblox
                nolak trade ke akun itu.
  * PERLU VERIFIKASI  semua yang bisa dicek publik lolos, tapi ada yang gak
                bisa dipastikan otomatis (status Plus/Premium & privasi
                trade) -- staff konfirmasi manual.

ITEM TUMBAL = item limited PALING MURAH milik pembeli (berapapun
nominalnya) yang bisa di-trade -- dipake sebagai sisi pembeli di trade.
Item yang lagi on-hold gak dihitung.

Yang TIDAK bisa dipastikan dari luar (jujur ditulis "belum bisa
diverifikasi", bukan ditebak): status Plus/Premium, pengaturan "siapa yang
boleh trade", 2-Step Verification, umur. Roblox cuma ngasih jawaban gabungan
lewat can-trade-with (butuh login), dan itu pun gak nyebut alasannya.
"""

from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field

import aiohttp

from bot.core.logger import logger
from bot.utils import roblox_api as api
from bot.utils.roblox_api import (
    RobloxAPIError,
    RobloxAuthError,
    RobloxInventoryHidden,
    RobloxRateLimited,
)

VERDICT_ELIGIBLE = "eligible"
VERDICT_BLOCKED = "blocked"
VERDICT_REVIEW = "review"

TOP_ITEMS_SHOWN = 5
TUMBAL_SHOWN = 3
CACHE_TTL_SECONDS = 90.0
MAX_CONCURRENT_CHECKS = 3

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
_PROFILE_URL_RE = re.compile(r"roblox\.com/(?:[a-z\-]{2,6}/)?users/(\d+)", re.IGNORECASE)
_ID_PREFIX_RE = re.compile(r"^id\s*[:=]\s*(\d{1,19})$", re.IGNORECASE)


# ============================================================================
# Input
# ============================================================================

def parse_roblox_identifier(text: str) -> tuple[str, str]:
    """Terima username, link profil (roblox.com/users/<id>/profile), atau
    `id:<angka>`. Return ("username", nama) / ("id", angka). Raise
    ValueError kalau formatnya gak dikenalin."""
    value = (text or "").strip()
    match = _PROFILE_URL_RE.search(value)
    if match:
        return "id", match.group(1)
    match = _ID_PREFIX_RE.match(value)
    if match:
        return "id", match.group(1)
    value = value.lstrip("@")
    if _USERNAME_RE.match(value):
        return "username", value
    raise ValueError(
        "Format gak dikenalin. Isi username Roblox (3-20 karakter), link profil, atau `id:<angka>`."
    )


# ============================================================================
# Model
# ============================================================================

@dataclass(slots=True)
class OwnedItem:
    asset_id: int | None
    name: str
    serial: int | None
    rap: int
    value: int
    valued: bool
    projected: bool
    on_hold: bool | None


@dataclass(slots=True)
class TradeCheckResult:
    profile: dict
    visibility: str  # "public" | "private"
    items: list[OwnedItem]
    truncated: bool
    total_rap: int
    total_value: int
    unique_count: int
    top_items: list[dict]
    tumbal_items: list[dict]
    tumbal_confirmed: bool  # True = daftar tumbal dari data "tradable" resmi Roblox
    official: dict | None  # hasil can-trade-with (None = verifikasi resmi gak jalan)
    verdict: str
    headline: str
    blockers: list[str]
    warnings: list[str]
    staff_notes: list[str]
    rolimons_ok: bool
    checked_at: int = field(default_factory=lambda: int(time.time()))


# ============================================================================
# Hitung & verdict (fungsi murni -- gampang dites)
# ============================================================================

def build_owned_items(raw_items: list[dict], rolimons: dict[int, dict]) -> list[OwnedItem]:
    """Gabungin data inventory Roblox dengan RAP/Value Rolimons. RAP dari
    Rolimons dipake kalau item-nya ada di sana (konsisten sama angka
    leaderboard Rolimons), kalau gak (misal UGC limited) pake RAP dari
    Roblox. Item yang belum di-value Rolimons dihitung sebesar RAP-nya."""
    owned: list[OwnedItem] = []
    for raw in raw_items:
        asset_id = raw.get("asset_id")
        roli = rolimons.get(asset_id) if asset_id is not None else None
        roblox_rap = int(raw.get("rap") or 0)
        rap = roli["rap"] if roli else roblox_rap
        valued = bool(roli and roli["valued"])
        value = roli["value"] if valued else rap
        owned.append(
            OwnedItem(
                asset_id=asset_id, name=str(raw.get("name", "?")), serial=raw.get("serial"),
                rap=rap, value=value, valued=valued,
                projected=bool(roli and roli["projected"]), on_hold=raw.get("on_hold"),
            )
        )
    return owned


def normalize_collectibles(rows: list[dict]) -> list[dict]:
    """Baris inventory.roblox.com/.../collectibles -> bentuk umum
    {asset_id, name, serial, rap, on_hold}."""
    normalized = []
    for row in rows:
        hold = row.get("isOnHold")
        normalized.append({
            "asset_id": _int_or_none(row.get("assetId")),
            "name": str(row.get("name", "?")),
            "serial": _int_or_none(row.get("serialNumber")),
            "rap": _int_or_none(row.get("recentAveragePrice")) or 0,
            "on_hold": hold if isinstance(hold, bool) else None,
        })
    return normalized


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def pick_tumbal(candidates: list[OwnedItem]) -> list[dict]:
    """Item tumbal = yang PALING MURAH (RAP terendah), berapapun nominalnya.
    Item on-hold dibuang. Yang ditampilin item BERBEDA (bukan 3 salinan dari
    item yang sama): salinan non-serial digabung dan jumlahnya dicatat
    (`count`); item bernomor seri (Limited U) tampil dengan nomornya.
    Urutan stabil: RAP naik, lalu nama."""
    usable = [item for item in candidates if item.on_hold is not True]
    usable.sort(key=lambda item: (item.rap, item.name.lower(), item.serial or 0))
    picked: dict[object, dict] = {}
    for item in usable:
        key = item.asset_id if item.asset_id is not None else item.name
        entry = picked.get(key)
        if entry:
            entry["count"] += 1
            entry["serial"] = None  # lebih dari satu salinan -- nomor seri satu-satu gak relevan
            continue
        if len(picked) >= TUMBAL_SHOWN:
            continue
        picked[key] = {"name": item.name, "rap": item.rap, "serial": item.serial, "count": 1}
    return list(picked.values())


def summarize_top(items: list[OwnedItem], limit: int = TOP_ITEMS_SHOWN) -> list[dict]:
    """Item termahal (urut Value, lalu RAP). Salinan non-serial dari item
    yang sama digabung (xN); item bernomor seri (Limited U) tampil satu-satu."""
    grouped: dict[tuple, dict] = {}
    for item in items:
        key = (item.asset_id, item.serial) if item.serial is not None else (item.asset_id, None, item.name)
        entry = grouped.get(key)
        if entry:
            entry["count"] += 1
        else:
            grouped[key] = {
                "name": item.name, "rap": item.rap, "value": item.value, "serial": item.serial,
                "projected": item.projected, "valued": item.valued, "count": 1,
            }
    ranked = sorted(grouped.values(), key=lambda e: (e["value"], e["rap"]), reverse=True)
    return ranked[:limit]


_OFFICIAL_BLOCK_REASON = (
    "Roblox menolak trade ke akun ini. Biasanya karena belum punya Roblox Plus/Premium aktif, "
    "pengaturan privasi trade, atau perlu verifikasi umur (Roblox tidak merinci penyebabnya)."
)


def evaluate(
    *,
    is_banned: bool,
    visibility: str,
    tumbal_candidates: int,
    official: dict | None,
) -> tuple[str, str, list[str], list[str], list[str]]:
    """Return (verdict, headline, blockers, warnings, staff_notes).

    blockers = penghalang PASTI (dilihat pembeli), warnings = hal yang belum
    bisa dipastikan (dilihat pembeli), staff_notes = info teknis (staff saja)."""
    blockers: list[str] = []
    warnings: list[str] = []
    staff_notes: list[str] = []

    if is_banned:
        blockers.append("Akun Roblox ini ter-banned.")
    if visibility == "private":
        blockers.append(
            "Inventory masih privat. Ubah di Roblox: Settings > Privacy > Who can see my inventory "
            "jadi Everyone, lalu cek ulang."
        )
    elif tumbal_candidates == 0:
        blockers.append(
            "Tidak ada item limited yang bisa dijadikan tumbal (kosong, atau semuanya sedang on-hold)."
        )

    official_ok = False
    if official is None:
        warnings.append(
            "Status Plus/Premium dan privasi trade belum bisa diverifikasi otomatis. "
            "Staff akan konfirmasi manual sebelum order diproses."
        )
    else:
        detail = official.get("detail")
        if detail == "eligible":
            official_ok = True
        elif detail in ("target_ineligible", "both_ineligible"):
            blockers.append(_OFFICIAL_BLOCK_REASON)
        else:
            warnings.append(
                "Status trade belum bisa diverifikasi otomatis. Staff akan konfirmasi manual."
            )
            staff_notes.append(
                f"Verifikasi resmi tidak konklusif (detail: {detail}). "
                "Cek akun penjual yang dipakai (ROBLOX_COOKIE): butuh Plus/Premium aktif, privasi trade "
                "tidak terlalu ketat, dan tidak butuh verifikasi umur."
            )

    if blockers:
        return VERDICT_BLOCKED, blockers[0], blockers, warnings, staff_notes
    if official_ok:
        return (
            VERDICT_ELIGIBLE,
            "Lolos verifikasi resmi Roblox, inventory publik, dan item tumbal tersedia.",
            blockers, warnings, staff_notes,
        )
    return (
        VERDICT_REVIEW,
        "Syarat yang bisa dicek otomatis sudah lolos, sisanya perlu dikonfirmasi staff.",
        blockers, warnings, staff_notes,
    )


# ============================================================================
# Pipeline
# ============================================================================

_semaphore = asyncio.Semaphore(MAX_CONCURRENT_CHECKS)
_cache: dict[int, tuple[float, TradeCheckResult]] = {}


async def _load_inventory(session: aiohttp.ClientSession, user_id: int) -> tuple[str, list[dict], bool]:
    visible = await api.fetch_inventory_visibility(session, user_id)
    if visible is False:
        return "private", [], False
    try:
        rows, truncated = await api.fetch_collectibles(session, user_id)
    except RobloxInventoryHidden:
        return "private", [], False
    return "public", normalize_collectibles(rows), truncated


async def _load_rolimons(session: aiohttp.ClientSession) -> tuple[dict[int, dict], bool, bool]:
    """Return (items, stale, ok). Gagal total gak ngegugurin hasil -- cuma
    Value-nya jatuh ke RAP & dikasih peringatan."""
    try:
        items, stale = await api.fetch_rolimons_items(session)
        return items, stale, True
    except RobloxRateLimited:
        raise
    except RobloxAPIError as exc:
        logger.warning("Rolimons gak bisa diakses buat trade check: %s", exc)
        return {}, False, False


async def _load_official(session: aiohttp.ClientSession, user_id: int) -> dict:
    """Verifikasi resmi (butuh cookie). Return {"can_trade": ..., "tradable":
    list|None, "errors": [str]} -- error per-bagian dicatat buat staff, gak
    pernah dilempar (verifikasi resmi itu bonus, bukan syarat jalan)."""
    out: dict = {"can_trade": None, "tradable": None, "errors": []}

    async def can_trade() -> None:
        try:
            out["can_trade"] = await api.fetch_can_trade_with(session, user_id)
        except RobloxAuthError:
            out["errors"].append("Cookie ditolak Roblox (401) -- ROBLOX_COOKIE kadaluarsa/salah, perlu diperbarui.")
        except RobloxRateLimited:
            out["errors"].append("can-trade-with kena rate limit (429).")
        except RobloxAPIError as exc:
            out["errors"].append(f"can-trade-with gagal: {exc}")

    async def tradable() -> None:
        try:
            out["tradable"] = await api.fetch_tradable_items(session, user_id)
        except RobloxInventoryHidden:
            out["errors"].append("tradableItems ditolak (403) -- inventory pembeli tidak bisa dilihat akun penjual.")
        except RobloxAuthError:
            pass  # sudah dicatat oleh can_trade()
        except RobloxRateLimited:
            out["errors"].append("tradableItems kena rate limit (429).")
        except RobloxAPIError as exc:
            out["errors"].append(f"tradableItems gagal: {exc}")

    await asyncio.gather(can_trade(), tradable())
    return out


async def run_trade_check(kind: str, value: str, *, force: bool = False) -> TradeCheckResult:
    """Jalanin pengecekan lengkap. `kind`/`value` dari parse_roblox_identifier.

    Raise RobloxUserNotFound / RobloxRateLimited / RobloxAPIError buat
    kegagalan di tahap yang gak bisa dilewatin (profil / inventory)."""
    async with _semaphore:
        profile = (
            await api.fetch_roblox_profile(value) if kind == "username" else await api.fetch_roblox_profile_by_id(int(value))
        )
        user_id = int(profile["id"])

        cached = _cache.get(user_id)
        if cached and not force and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]

        async with aiohttp.ClientSession() as session:
            inventory_task = _load_inventory(session, user_id)
            rolimons_task = _load_rolimons(session)
            if api.has_trade_credentials():
                official_task = _load_official(session, user_id)
                (visibility, raw_items, truncated), (rolimons, stale, rolimons_ok), official_raw = await asyncio.gather(
                    inventory_task, rolimons_task, official_task
                )
            else:
                (visibility, raw_items, truncated), (rolimons, stale, rolimons_ok) = await asyncio.gather(
                    inventory_task, rolimons_task
                )
                official_raw = None

    result = _assemble(profile, visibility, raw_items, truncated, rolimons, stale, rolimons_ok, official_raw)
    _cache[user_id] = (time.monotonic(), result)
    return result


def _assemble(
    profile: dict, visibility: str, raw_items: list[dict], truncated: bool,
    rolimons: dict[int, dict], stale: bool, rolimons_ok: bool, official_raw: dict | None,
) -> TradeCheckResult:
    items = build_owned_items(raw_items, rolimons)

    # Daftar tumbal: dari data "tradable" resmi Roblox kalau ada (sudah
    # ngebuang item yang gak bisa di-trade), kalau gak dari inventory publik.
    tumbal_confirmed = False
    tumbal_pool = items
    if official_raw is not None and official_raw.get("tradable") is not None:
        tumbal_pool = build_owned_items(official_raw["tradable"], rolimons)
        tumbal_confirmed = True
    tumbal = pick_tumbal(tumbal_pool) if visibility == "public" else []
    candidates = len([i for i in tumbal_pool if i.on_hold is not True]) if visibility == "public" else 0

    official = official_raw["can_trade"] if official_raw is not None else None
    verdict, headline, blockers, warnings, staff_notes = evaluate(
        is_banned=bool(profile.get("is_banned")), visibility=visibility,
        tumbal_candidates=candidates, official=official,
    )
    if official_raw is None:
        staff_notes.append(
            "Verifikasi resmi nonaktif (ROBLOX_COOKIE belum diatur) -- verdict maksimal PERLU VERIFIKASI."
        )
    elif official_raw["errors"]:
        staff_notes.extend(official_raw["errors"])

    if truncated:
        warnings.append("Inventory sangat besar, total RAP/Value dihitung dari sebagian item saja.")
    if not rolimons_ok:
        warnings.append("Data Rolimons sedang tidak tersedia, Value dihitung sebesar RAP.")
    elif stale:
        staff_notes.append("Data Rolimons memakai cache lama (fetch terbaru gagal).")

    return TradeCheckResult(
        profile=profile, visibility=visibility, items=items, truncated=truncated,
        total_rap=sum(i.rap for i in items), total_value=sum(i.value for i in items),
        unique_count=len({i.asset_id for i in items if i.asset_id is not None}),
        top_items=summarize_top(items),
        tumbal_items=tumbal,
        tumbal_confirmed=tumbal_confirmed, official=official, verdict=verdict, headline=headline,
        blockers=blockers, warnings=warnings, staff_notes=staff_notes, rolimons_ok=rolimons_ok,
    )


def format_created(iso_str: str | None) -> tuple[str, str]:
    """(tanggal dibaca manusia, umur akun) dari timestamp ISO 8601 Roblox --
    misal ("17 Apr 2013", "12 tahun lalu")."""
    if not iso_str:
        return "?", ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except ValueError:
        return iso_str, ""
    days = (datetime.now(timezone.utc) - dt).days
    years = days // 365
    age = f"{years} tahun lalu" if years >= 1 else f"{max(days, 0)} hari lalu"
    return dt.strftime("%d %b %Y"), age


def result_to_card(result: TradeCheckResult) -> dict:
    """Ubah hasil pengecekan jadi dict polos buat bot.ui.components.
    roblox_trade_check_container() -- 4 baris "Syarat Trade" dengan status
    ok/bad/unknown + teksnya DITENTUIN di sini (bukan di UI) biar semua
    aturan tampilan status ada di satu tempat."""
    created_display, age = format_created(result.profile.get("created"))
    profile = {**result.profile, "created_display": created_display, "account_age_display": age}

    banned = bool(profile.get("is_banned"))
    rows = [
        {"label": "Akun Roblox", "state": "bad" if banned else "ok",
         "text": "KEBAN di Roblox" if banned else f"Aman, dibuat {created_display} ({age})".replace(" ()", "")},
        {"label": "Inventory", "state": "ok" if result.visibility == "public" else "bad",
         "text": "Publik" if result.visibility == "public" else "Privat"},
    ]

    detail = (result.official or {}).get("detail")
    if detail == "eligible":
        rows.append({"label": "Plus/Premium & Izin Trade", "state": "ok",
                     "text": "Diizinkan Roblox (verifikasi resmi)"})
    elif detail in ("target_ineligible", "both_ineligible"):
        rows.append({"label": "Plus/Premium & Izin Trade", "state": "bad",
                     "text": "Ditolak Roblox, kemungkinan belum Plus/Premium atau privasi trade"})
    else:
        rows.append({"label": "Plus/Premium & Izin Trade", "state": "unknown",
                     "text": "Belum bisa diverifikasi otomatis"})

    if result.visibility != "public":
        rows.append({"label": "Item Tumbal", "state": "unknown", "text": "Belum bisa dicek (inventory privat)"})
    elif result.tumbal_items:
        cheapest = result.tumbal_items[0]
        suffix = "terverifikasi bisa di-trade" if result.tumbal_confirmed else "status trade item belum diverifikasi resmi"
        rows.append({"label": "Item Tumbal", "state": "ok",
                     "text": f"Ada, termurah {cheapest['name']} (RAP {cheapest['rap']:,}), {suffix}"})
    else:
        rows.append({"label": "Item Tumbal", "state": "bad", "text": "Tidak ada"})

    return {
        "profile": profile,
        "verdict": result.verdict,
        "headline": result.headline,
        "rows": rows,
        "visibility": result.visibility,
        "stats": {
            "total_items": len(result.items), "unique_items": result.unique_count,
            "total_rap": result.total_rap, "total_value": result.total_value,
        },
        "top_items": result.top_items,
        "tumbal_items": result.tumbal_items,
        "tumbal_confirmed": result.tumbal_confirmed,
        "blockers": result.blockers,
        "warnings": result.warnings,
        "staff_notes": result.staff_notes,
        "checked_at": result.checked_at,
    }


# ============================================================================
# Diagnostik (/tradecheck debug) -- respons mentah tiap sumber, buat
# validasi pertama kali di server asli.
# ============================================================================

async def run_diagnostics(kind: str, value: str) -> list[str]:
    lines: list[str] = []
    lines.append(f"Cookie diatur: **{'ya' if api.has_trade_credentials() else 'tidak'}**")
    lines.append(f"Proxy diatur: **{'ya' if api.config.roblox_proxy else 'tidak'}**")
    try:
        profile = (
            await api.fetch_roblox_profile(value) if kind == "username" else await api.fetch_roblox_profile_by_id(int(value))
        )
    except Exception as exc:  # noqa: BLE001
        return lines + [f"Profil: GAGAL ({type(exc).__name__}: {exc})"]
    user_id = int(profile["id"])
    lines.append(f"Profil: OK -- @{profile['name']} (ID {user_id}), banned={profile['is_banned']}")

    async with aiohttp.ClientSession() as session:
        async def probe(label: str, coro_factory):
            try:
                lines.append(f"{label}: {await coro_factory()}")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"{label}: GAGAL ({type(exc).__name__}: {str(exc)[:160]})")

        await probe("can-view-inventory", lambda: _fmt(api.fetch_inventory_visibility(session, user_id)))

        async def collectibles():
            rows, truncated = await api.fetch_collectibles(session, user_id)
            sample = rows[0] if rows else {}
            return f"{len(rows)} item, terpotong={truncated}, field contoh={sorted(sample.keys())[:12]}"
        await probe("collectibles", collectibles)

        async def rolimons():
            items, stale = await api.fetch_rolimons_items(session)
            age = api.rolimons_cache_age()
            return f"{len(items)} item, stale={stale}, umur cache={int(age) if age is not None else '?'}s"
        await probe("rolimons", rolimons)

        if api.has_trade_credentials():
            await probe("can-trade-with", lambda: _fmt(api.fetch_can_trade_with(session, user_id)))

            async def tradable():
                rows = await api.fetch_tradable_items(session, user_id)
                held = sum(1 for r in rows if r["on_hold"] is True)
                return f"{len(rows)} instance, on-hold={held}"
            await probe("tradableItems", tradable)
        else:
            lines.append("can-trade-with / tradableItems: dilewati (ROBLOX_COOKIE belum diatur)")
    return lines


async def _fmt(awaitable) -> str:
    return str(await awaitable)
