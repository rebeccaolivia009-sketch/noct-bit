"""Klien HTTP tipis buat Roblox Public API -- dipake bot.cogs.
roblox_profile (/checkprofile). Semua endpoint di sini publik & gak
butuh API key.

PENTING soal saldo Robux: Roblox GAK PERNAH nge-expose saldo Robux akun
ORANG LAIN lewat API publik manapun -- itu data privat yang cuma
keliatan lewat endpoint economy.roblox.com/v1/user/currency, dan itu
WAJIB pake cookie autentikasi (.ROBLOSECURITY) punya akun itu sendiri.
Gak ada cara legit buat bot pihak ketiga liat saldo Robux akun orang
lain. Makanya modul ini SENGAJA gak nyoba fetch/nebak itu -- caller
(bot.cogs.roblox_profile) nampilin field itu sebagai "gak bisa dicek",
bukan angka yang keliatan valid padahal karangan."""

from __future__ import annotations

import aiohttp

USERNAME_LOOKUP_URL = "https://users.roblox.com/v1/usernames/users"
USER_PROFILE_URL = "https://users.roblox.com/v1/users/{user_id}"
AVATAR_HEADSHOT_URL = "https://thumbnails.roblox.com/v1/users/avatar-headshot"


class RobloxUserNotFound(Exception):
    """Username-nya gak ketemu sama sekali di Roblox."""


class RobloxAPIError(Exception):
    """Roblox API-nya error/gak bisa diakses -- BUKAN berarti usernamenya gak ada."""


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

        try:
            async with session.get(
                USER_PROFILE_URL.format(user_id=user_id), timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
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
        "name": profile.get("name", username),
        "display_name": profile.get("displayName", username),
        "created": profile.get("created"),
        "is_banned": bool(profile.get("isBanned", False)),
        "has_verified_badge": bool(profile.get("hasVerifiedBadge", False)),
        "avatar_url": avatar_url,
    }
