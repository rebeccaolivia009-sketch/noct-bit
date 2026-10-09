"""Klien HTTP tipis buat Roblox Public API + Rolimons -- dipake bot.cogs.
roblox_profile (/checkprofile) dan bot.cogs.roblox_check (/tradecheck).

DUA LAPIS data:

1. PUBLIK (gak butuh login, selalu jalan): profil, inventory publik atau
   privat, daftar limited (collectibles), RAP & Value (Rolimons).

2. TERAUTENTIKASI (OPSIONAL, cuma aktif kalau ROBLOX_COOKIE diatur --
   lihat bot.core.config): trades.roblox.com -- "bisa trade dengan user
   ini atau enggak" (jawaban resmi dari Roblox, nyakup Plus/Premium,
   privasi, umur) dan daftar item yang BENERAN bisa di-trade (isOnHold).
   Endpoint ini emang gak bisa diakses tanpa login.

PENTING soal saldo Robux: Roblox GAK PERNAH nge-expose saldo Robux akun
ORANG LAIN lewat API manapun -- itu data privat yang cuma keliatan lewat
endpoint economy.roblox.com/v1/user/currency, dan itu WAJIB pake cookie
autentikasi punya akun itu sendiri. Modul ini SENGAJA gak nyoba fetch/
nebak itu -- caller nampilin field itu sebagai "gak bisa dicek", bukan
angka yang keliatan valid padahal karangan.

PENTING soal cookie: nilainya TIDAK PERNAH di-log, di-print, atau masuk ke
pesan error -- cuma dipasang di header Cookie saat request ke
trades.roblox.com. Respons Roblox bisa aja ganti format tanpa pemberitahuan
(endpoint trades itu legacy/cookie-auth), makanya semua parsing di sini
defensif dan ada command /tradecheck debug buat ngecek respons mentahnya."""

from __future__ import annotations

import asyncio
import time

import aiohttp

from bot.core.config import config

USERNAME_LOOKUP_URL = "https://users.roblox.com/v1/usernames/users"
USER_PROFILE_URL = "https://users.roblox.com/v1/users/{user_id}"
AVATAR_HEADSHOT_URL = "https://thumbnails.roblox.com/v1/users/avatar-headshot"

CAN_VIEW_INVENTORY_URL = "https://inventory.roblox.com/v1/users/{user_id}/can-view-inventory"
COLLECTIBLES_URL = "https://inventory.roblox.com/v1/users/{user_id}/assets/collectibles"
ROLIMONS_ITEMS_URL = "https://api.rolimons.com/items/v2/itemdetails"
TRADE_CAN_TRADE_WITH_V2_URL = "https://trades.roblox.com/v2/users/{user_id}/can-trade-with"
TRADE_CAN_TRADE_WITH_V1_URL = "https://trades.roblox.com/v1/users/{user_id}/can-trade-with"
TRADE_TRADABLE_ITEMS_URL = "https://trades.roblox.com/v2/users/{user_id}/tradableItems"

USER_AGENT = "NoctraBot/1.0 (Discord store bot; trade checker)"

# Rolimons: rate limit 10 request/menit dan datanya di-cache server 60 detik
# -- jadi cache lokal 5 menit udah lebih dari cukup & aman.
ROLIMONS_CACHE_TTL = 300.0

DEFAULT_TIMEOUT = 10.0


class RobloxUserNotFound(Exception):
    """Username/ID-nya gak ketemu sama sekali di Roblox."""


class RobloxAPIError(Exception):
    """Roblox API-nya error/gak bisa diakses -- BUKAN berarti usernamenya gak ada."""


class RobloxRateLimited(RobloxAPIError):
    """Roblox/Rolimons nge-balikin 429 (kebanyakan request) -- coba lagi bentar."""


class RobloxAuthError(RobloxAPIError):
    """Endpoint terautentikasi nolak cookie (401) -- cookie kadaluarsa/salah."""


class RobloxInventoryHidden(Exception):
    """Inventory user-nya privat (403) -- bukan error, itu hasil valid."""


# ============================================================================
# HTTP helper
# ============================================================================

def has_trade_credentials() -> bool:
    """True kalau ROBLOX_COOKIE diatur -- aktifin lapis verifikasi resmi."""
    return bool(_clean_cookie(config.roblox_cookie))


def _clean_cookie(raw: str) -> str:
    """Rapihin cookie yang di-paste dengan berbagai bentuk: pake tanda
    kutip, ada prefix `.ROBLOSECURITY=`, ada spasi/newline nyasar."""
    value = (raw or "").strip().strip('"').strip("'")
    if value.upper().startswith(".ROBLOSECURITY="):
        value = value.split("=", 1)[1]
    return "".join(value.split())


def _auth_headers() -> dict[str, str]:
    return {"Cookie": f".ROBLOSECURITY={_clean_cookie(config.roblox_cookie)}"}


def _retry_after(resp: aiohttp.ClientResponse) -> float:
    try:
        return min(max(float(resp.headers.get("Retry-After", "2")), 1.0), 5.0)
    except ValueError:
        return 2.0


async def _request_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    retries: int = 1,
    timeout: float = DEFAULT_TIMEOUT,
):
    """GET `url` -> (status, json-atau-None). Otomatis nunggu & coba lagi
    sekali kalau kena 429; kalau masih 429 -> RobloxRateLimited. Error
    jaringan/timeout -> RobloxAPIError (pesannya GAK nyertain header/cookie)."""
    proxy = config.roblox_proxy or None
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    for attempt in range(retries + 1):
        try:
            async with session.get(
                url, params=params, headers=merged, proxy=proxy, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                if resp.status == 429:
                    if attempt < retries:
                        await asyncio.sleep(_retry_after(resp))
                        continue
                    raise RobloxRateLimited("Kena rate limit (429).")
                try:
                    data = await resp.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    data = None
                return resp.status, data
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise RobloxAPIError(f"Gagal konek ({type(exc).__name__}).") from exc
    raise RobloxRateLimited("Kena rate limit (429).")  # pragma: no cover


# ============================================================================
# Profil (publik)
# ============================================================================

async def _load_profile(session: aiohttp.ClientSession, user_id: int, fallback_name: str) -> dict:
    try:
        async with session.get(
            USER_PROFILE_URL.format(user_id=user_id), timeout=aiohttp.ClientTimeout(total=8)
        ) as resp:
            if resp.status in (400, 404):
                raise RobloxUserNotFound(str(user_id))
            if resp.status != 200:
                raise RobloxAPIError(f"Roblox API balikin status {resp.status} pas ambil detail profil.")
            profile = await resp.json()
    except aiohttp.ClientError as exc:
        raise RobloxAPIError(f"Gagal konek ke Roblox: {exc}") from exc

    # Avatar OPSIONAL -- gagal ambil ini gak boleh gugurin seluruh
    # hasil checker, staff masih dapet sisa datanya.
    avatar_url = None
    try:
        async with session.get(
            AVATAR_HEADSHOT_URL,
            params={"userIds": str(user_id), "size": "420x420", "format": "Png", "isCircular": "false"},
            timeout=aiohttp.ClientTimeout(total=8),
        ) as resp:
            if resp.status == 200:
                avatar_data = await resp.json()
                items = avatar_data.get("data") or []
                if items and items[0].get("state") == "Completed":
                    avatar_url = items[0].get("imageUrl")
    except aiohttp.ClientError:
        avatar_url = None

    return {
        "id": user_id,
        "name": profile.get("name", fallback_name),
        "display_name": profile.get("displayName", fallback_name),
        "created": profile.get("created"),
        "is_banned": bool(profile.get("isBanned", False)),
        "has_verified_badge": bool(profile.get("hasVerifiedBadge", False)),
        "avatar_url": avatar_url,
    }


async def fetch_roblox_profile(username: str) -> dict:
    """Return dict {id, name, display_name, created, is_banned,
    has_verified_badge, avatar_url} kalau username-nya ketemu.

    Raise RobloxUserNotFound kalau usernamenya gak ada, RobloxAPIError
    kalau Roblox-nya lagi bermasalah atau network error -- dua-duanya
    HARUS dibedain sama caller (pesan errornya beda: "gak ketemu" vs
    "coba lagi bentar")."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                USERNAME_LOOKUP_URL,
                json={"usernames": [username], "excludeBannedUsers": False},
                timeout=aiohttp.ClientTimeout(total=8),
            ) as resp:
                if resp.status != 200:
                    raise RobloxAPIError(f"Roblox API balikin status {resp.status} pas cari username.")
                lookup_data = await resp.json()
        except aiohttp.ClientError as exc:
            raise RobloxAPIError(f"Gagal konek ke Roblox: {exc}") from exc

        matches = lookup_data.get("data") or []
        if not matches:
            raise RobloxUserNotFound(username)
        user_id = matches[0]["id"]

        return await _load_profile(session, user_id, username)


async def fetch_roblox_profile_by_id(user_id: int) -> dict:
    """Sama kayak fetch_roblox_profile() tapi lewat User ID (misal dari link
    profil roblox.com/users/<id>/profile). Error-nya sama persis."""
    async with aiohttp.ClientSession() as session:
        return await _load_profile(session, user_id, str(user_id))


# ============================================================================
# Inventory (publik)
# ============================================================================

async def fetch_inventory_visibility(session: aiohttp.ClientSession, user_id: int) -> bool | None:
    """True = inventory publik, False = privat, None = gak bisa dipastikan
    (endpoint-nya error) -- caller lanjut coba fetch collectibles & tanya
    hasilnya (403 = privat)."""
    status, data = await _request_json(session, CAN_VIEW_INVENTORY_URL.format(user_id=user_id))
    if status == 200 and isinstance(data, dict) and isinstance(data.get("canView"), bool):
        return data["canView"]
    return None


async def fetch_collectibles(
    session: aiohttp.ClientSession, user_id: int, *, max_pages: int = 30
) -> tuple[list[dict], bool]:
    """Semua limited (Limited & Limited U) milik user, 100 per halaman.
    Return (items, truncated) -- truncated=True kalau kena batas `max_pages`
    (inventory super gede) jadi totalnya cuma sebagian.

    Raise RobloxInventoryHidden kalau inventory privat (403)."""
    items: list[dict] = []
    cursor: str | None = None
    for _ in range(max_pages):
        params = {"limit": "100", "sortOrder": "Asc"}
        if cursor:
            params["cursor"] = cursor
        status, data = await _request_json(session, COLLECTIBLES_URL.format(user_id=user_id), params=params)
        if status == 403:
            raise RobloxInventoryHidden(str(user_id))
        if status != 200 or not isinstance(data, dict):
            raise RobloxAPIError(f"Roblox balikin status {status} pas ambil inventory.")
        items.extend(row for row in (data.get("data") or []) if isinstance(row, dict))
        cursor = data.get("nextPageCursor")
        if not cursor:
            return items, False
    return items, True


# ============================================================================
# Rolimons (RAP & Value) -- cache lokal
# ============================================================================

_rolimons_cache: dict = {"fetched_at": 0.0, "items": {}}
_rolimons_lock = asyncio.Lock()

_DEMAND_LABELS = {0: "Terrible", 1: "Low", 2: "Normal", 3: "High", 4: "Amazing"}


def _parse_rolimons(payload: dict) -> dict[int, dict]:
    """Format v2: items[<id>] = [nama, akronim, RAP, valued(-1 = belum
    di-value), Value, demand, trend, projected(1/-1), hyped, rare, versi].
    Baris yang formatnya gak sesuai dilewat -- bukan alasan nolak semuanya."""
    parsed: dict[int, dict] = {}
    for key, codes in (payload.get("items") or {}).items():
        try:
            if not isinstance(codes, list) or len(codes) < 10:
                continue
            valued = int(codes[3]) != -1
            parsed[int(key)] = {
                "name": str(codes[0]),
                "rap": max(int(codes[2]), 0),
                "valued": valued,
                "value": max(int(codes[4]), 0),
                "demand": _DEMAND_LABELS.get(int(codes[5])),
                "projected": int(codes[7]) == 1,
            }
        except (TypeError, ValueError):
            continue
    return parsed


async def fetch_rolimons_items(session: aiohttp.ClientSession) -> tuple[dict[int, dict], bool]:
    """Return (items{asset_id: {...}}, stale). `stale`=True kalau fetch baru
    gagal & yang dipake cache lama. Raise RobloxAPIError kalau gagal DAN
    belum pernah punya cache sama sekali."""
    async with _rolimons_lock:
        age = time.monotonic() - _rolimons_cache["fetched_at"]
        if _rolimons_cache["items"] and age < ROLIMONS_CACHE_TTL:
            return _rolimons_cache["items"], False
        try:
            status, data = await _request_json(session, ROLIMONS_ITEMS_URL)
            if status != 200 or not isinstance(data, dict) or not data.get("success", True):
                raise RobloxAPIError(f"Rolimons balikin status {status}.")
            items = _parse_rolimons(data)
            if not items:
                raise RobloxAPIError("Data Rolimons kosong/formatnya berubah.")
        except RobloxAPIError:
            if _rolimons_cache["items"]:
                return _rolimons_cache["items"], True
            raise
        _rolimons_cache["items"] = items
        _rolimons_cache["fetched_at"] = time.monotonic()
        return items, False


def rolimons_cache_age() -> float | None:
    """Umur cache Rolimons dalam detik (None kalau belum pernah di-fetch)."""
    if not _rolimons_cache["items"]:
        return None
    return time.monotonic() - _rolimons_cache["fetched_at"]


# ============================================================================
# Trades (TERAUTENTIKASI -- butuh ROBLOX_COOKIE)
# ============================================================================

# Normalisasi jawaban v1 (angka) & v2 (string) ke satu set kata.
_V2_ELIGIBILITY = {
    "Eligible": "eligible",
    "CallingUserIneligible": "calling_ineligible",
    "TargetUserIneligible": "target_ineligible",
    "CannotTradeWithSelf": "self",
    "CallingUserPrivacySettingsRestricted": "privacy_restricted",
    "CallingUserAgeCheckRequired": "age_check_calling",
}
_V1_STATUS = {
    1: "eligible",
    2: "self",
    3: "calling_ineligible",
    4: "target_ineligible",
    5: "privacy_restricted",
    6: "both_ineligible",
    8: "age_check_calling",
}


def _normalize_can_trade_with(data: dict, source: str) -> dict:
    if source == "v2":
        detail = _V2_ELIGIBILITY.get(str(data.get("mutualTradeEligibility")), "unknown")
    else:
        try:
            detail = _V1_STATUS.get(int(data.get("status")), "unknown")
        except (TypeError, ValueError):
            detail = "unknown"
    can_trade = data.get("canTrade")
    if can_trade is True:
        detail = "eligible"
    return {"can_trade": bool(can_trade) if isinstance(can_trade, bool) else None, "detail": detail, "source": source}


async def fetch_can_trade_with(session: aiohttp.ClientSession, target_user_id: int) -> dict:
    """Jawaban RESMI Roblox: apakah akun penjual (pemilik cookie) bisa trade
    dengan `target_user_id`. Return {"can_trade": bool|None, "detail": str,
    "source": "v2"|"v1"}. `detail`: eligible / target_ineligible /
    calling_ineligible / privacy_restricted / age_check_calling / self /
    both_ineligible / unknown.

    Raise RobloxAuthError kalau cookie ditolak (401)."""
    headers = _auth_headers()
    status, data = await _request_json(session, TRADE_CAN_TRADE_WITH_V2_URL.format(user_id=target_user_id), headers=headers)
    if status == 200 and isinstance(data, dict):
        return _normalize_can_trade_with(data, "v2")
    if status == 401:
        raise RobloxAuthError("Cookie ditolak Roblox (401).")
    if status in (404, 405):
        # v2 belum/tidak tersedia -- coba v1 (deprecated tapi masih ada).
        status, data = await _request_json(
            session, TRADE_CAN_TRADE_WITH_V1_URL.format(user_id=target_user_id), headers=headers
        )
        if status == 200 and isinstance(data, dict):
            return _normalize_can_trade_with(data, "v1")
        if status == 401:
            raise RobloxAuthError("Cookie ditolak Roblox (401).")
    raise RobloxAPIError(f"can-trade-with balikin status {status}.")


async def fetch_tradable_items(
    session: aiohttp.ClientSession, target_user_id: int, *, max_pages: int = 20
) -> list[dict]:
    """Item milik `target_user_id` yang BENERAN bisa di-trade (menurut
    Roblox), per-instance. Tiap elemen: {asset_id, name, serial, rap,
    on_hold}. `on_hold` None = Roblox gak ngasih tau.

    Raise RobloxAuthError (401), RobloxInventoryHidden (403/gak berhak
    liat)."""
    headers = _auth_headers()
    flat: list[dict] = []
    cursor: str | None = None
    for _ in range(max_pages):
        params: dict[str, str] = {"limit": "100"}
        if cursor:
            params["cursor"] = cursor
        status, data = await _request_json(
            session, TRADE_TRADABLE_ITEMS_URL.format(user_id=target_user_id), params=params, headers=headers
        )
        if status == 401:
            raise RobloxAuthError("Cookie ditolak Roblox (401).")
        if status == 403:
            raise RobloxInventoryHidden(str(target_user_id))
        if status != 200 or not isinstance(data, dict):
            raise RobloxAPIError(f"tradableItems balikin status {status}.")

        for item in data.get("items") or []:
            if not isinstance(item, dict):
                continue
            target = item.get("itemTarget") or {}
            base_asset = _to_int(target.get("targetId"))
            instances = item.get("instances") or []
            if not instances:
                flat.append({
                    "asset_id": base_asset, "name": str(item.get("itemName", "?")), "serial": None,
                    "rap": _to_int(item.get("recentAveragePrice")) or 0, "on_hold": None,
                })
                continue
            for inst in instances:
                if not isinstance(inst, dict):
                    continue
                inst_target = inst.get("itemTarget") or {}
                hold = inst.get("isOnHold")
                flat.append({
                    "asset_id": _to_int(inst_target.get("targetId")) or base_asset,
                    "name": str(inst.get("itemName") or item.get("itemName", "?")),
                    "serial": _to_int(inst.get("serialNumber")),
                    "rap": _to_int(inst.get("recentAveragePrice")) or _to_int(item.get("recentAveragePrice")) or 0,
                    "on_hold": hold if isinstance(hold, bool) else None,
                })
        cursor = data.get("nextPageCursor")
        if not cursor:
            break
    return flat


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
