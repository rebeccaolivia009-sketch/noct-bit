"""
Logika murni (tanpa Discord/DB) buat Auto Reaction: nge-parse emoji dari
teks, validasi, filter pesan, dan diff. Dipakai panel (bot.ui.autoreact)
dan engine (bot.cogs.autoreact), dites sendiri.
"""

from __future__ import annotations

import re

from bot.utils.builder_spec import InputSpec
from bot.utils.validators import is_valid_emoji

MAX_EMOJIS = 5   # tiap emoji = 1 panggilan API per pesan; lebih dari ini bikin channel ramai ketinggalan

MODES = ("all", "media", "links")
MODE_LABEL = {
    "all": "Semua pesan",
    "media": "Pesan dengan gambar/lampiran",
    "links": "Pesan dengan link",
}
MODE_SHORT = {"all": "Semua pesan", "media": "Ada lampiran", "links": "Ada link"}

FORMS = {
    "emojis": (
        InputSpec("emojis", "Emoji (pisahkan dengan spasi)", required=True, max_length=300,
                  placeholder=f"Contoh: \U0001F44D \u2764\uFE0F \U0001F525  (maks {MAX_EMOJIS}, emoji custom server juga bisa)"),
    ),
}

KEY_LABEL = {"emojis": "Emoji", "filter_mode": "Filter pesan", "ignore_bots": "Abaikan bot", "enabled": "Status"}

_CUSTOM = re.compile(r"<a?:\w{2,32}:(\d{15,21})>")
_CUSTOM_ANY = re.compile(r"<a?:\w{2,32}:\d{15,21}>")
_KEYCAP = re.compile(r"^[0-9#*]\uFE0F?\u20E3$")
_URL = re.compile(r"https?://\S+", re.I)


def new_values() -> dict:
    return {"emojis": [], "filter_mode": "all", "ignore_bots": 1, "enabled": 1}


def custom_emoji_id(emoji: str) -> int | None:
    m = _CUSTOM.fullmatch(emoji)
    return int(m.group(1)) if m else None


def _clusters(chunk: str) -> list[str]:
    """Pecah rangkaian karakter jadi satuan emoji: pasangan bendera,
    gabungan ZWJ (keluarga, profesi), skin tone, variation selector, dan keycap."""
    out: list[str] = []
    i, n = 0, len(chunk)
    while i < n:
        cur = chunk[i]
        cp = ord(cur)
        i += 1
        if 0x1F1E6 <= cp <= 0x1F1FF and i < n and 0x1F1E6 <= ord(chunk[i]) <= 0x1F1FF:
            cur += chunk[i]
            i += 1
        while i < n:
            c = ord(chunk[i])
            if c in (0xFE0F, 0x20E3) or 0x1F3FB <= c <= 0x1F3FF or 0xE0020 <= c <= 0xE007F:
                cur += chunk[i]
                i += 1
            elif c == 0x200D and i + 1 < n:
                cur += chunk[i] + chunk[i + 1]
                i += 2
            else:
                break
        out.append(cur)
    return out


def parse_emojis(text: str | None) -> tuple[list[str], list[str]]:
    """Teks -> (daftar emoji valid tanpa duplikat, daftar error). Emoji
    boleh dipisah spasi atau nempel (👍❤️🔥)."""
    emojis: list[str] = []
    errors: list[str] = []
    for token in (text or "").split():
        # Emoji custom bisa nempel emoji lain (👍<:pepe:123>🔥): pecah per posisinya.
        pieces: list[str] = []
        pos = 0
        for m in _CUSTOM_ANY.finditer(token):
            if m.start() > pos:
                pieces.extend(_clusters(token[pos:m.start()]))
            pieces.append(m.group(0))
            pos = m.end()
        if pos < len(token):
            pieces.extend(_clusters(token[pos:]))
        for piece in pieces:
            if _CUSTOM.fullmatch(piece) or _KEYCAP.match(piece) or is_valid_emoji(piece):
                if piece not in emojis:
                    emojis.append(piece)
            else:
                errors.append(f"'{piece}' bukan emoji.")
    if len(emojis) > MAX_EMOJIS:
        errors.append(f"Maksimal {MAX_EMOJIS} emoji (kamu masukin {len(emojis)}).")
    return emojis, errors


def apply_form(raw: dict) -> tuple[dict, list[str]]:
    """Jawaban modal -> (nilai buat draft, error). Kalau ada emoji yang
    salah, daftar lama DIPERTAHANKAN (jangan setengah-setengah)."""
    emojis, errors = parse_emojis(raw.get("emojis"))
    if errors:
        return {}, errors
    if not emojis:
        return {}, ["Emoji wajib diisi."]
    return {"emojis": emojis}, []


def validate(values: dict) -> list[str]:
    problems = []
    emojis = values.get("emojis") or []
    if not emojis:
        problems.append("Belum ada emoji.")
    elif len(emojis) > MAX_EMOJIS:
        problems.append(f"Maksimal {MAX_EMOJIS} emoji.")
    if values.get("filter_mode") not in MODES:
        problems.append("Filter pesan gak valid.")
    return problems


def matches(mode: str, content: str, has_media: bool) -> bool:
    """Apakah pesan ini kena auto reaction. `has_media` = ada lampiran atau
    embed gambar/video."""
    if mode == "media":
        return has_media
    if mode == "links":
        return bool(_URL.search(content or ""))
    return True


def cycle_mode(current: str) -> str:
    i = MODES.index(current) if current in MODES else -1
    return MODES[(i + 1) % len(MODES)]


def fmt(key: str, value) -> str:
    if key == "emojis":
        return " ".join(value) if value else "-"
    if key == "filter_mode":
        return MODE_LABEL.get(value, str(value))
    if key in ("ignore_bots",):
        return "ya" if value else "tidak"
    if key == "enabled":
        return "aktif" if value else "nonaktif"
    return str(value)


def _norm(key: str, value):
    if key == "emojis":
        return list(value or [])
    if isinstance(value, bool):
        return int(value)
    return value


def diff(original: dict | None, values: dict) -> list[str]:
    if original is None:
        return []
    lines = []
    for key in new_values():
        if _norm(key, original.get(key)) != _norm(key, values.get(key)):
            lines.append(
                f"\u2022 **{KEY_LABEL[key]}**: `{fmt(key, original.get(key))}` \u2192 `{fmt(key, values.get(key))}`"
            )
    return lines


def values_from_row(row, emojis: list[str]) -> dict:
    return {
        "emojis": list(emojis),
        "filter_mode": row["filter_mode"],
        "ignore_bots": int(row["ignore_bots"]),
        "enabled": int(row["enabled"]),
    }
