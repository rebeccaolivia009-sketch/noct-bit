"""Logic validasi buat dynamic input field produk yang diatur admin, plus
beberapa validator kecil yang dipake bareng di command lain (misal warna
aksen embed)."""

from __future__ import annotations

import re
import unicodedata

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CUSTOM_EMOJI_RE = re.compile(r"^<a?:\w{2,32}:(\d{15,21})>$")
_HEX_COLOR_RE = re.compile(r"^#?[0-9a-fA-F]{6}$")


class FieldValidationError(ValueError):
    pass


def validate_field_value(
    value: str,
    *,
    required: bool,
    min_length: int,
    max_length: int,
    validation: str,
    label: str,
) -> str:
    """Validasi satu value field yang disubmit, return value yang udah dibersihin."""
    value = (value or "").strip()

    if not value:
        if required:
            raise FieldValidationError(f"{label} wajib diisi.")
        return value

    if len(value) < min_length:
        raise FieldValidationError(f"{label} minimal {min_length} karakter.")
    if len(value) > max_length:
        raise FieldValidationError(f"{label} maksimal {max_length} karakter.")

    if validation == "numeric" and not value.isdigit():
        raise FieldValidationError(f"{label} cuma boleh angka.")
    elif validation == "alpha" and not value.isalpha():
        raise FieldValidationError(f"{label} cuma boleh huruf.")
    elif validation == "alphanumeric" and not value.isalnum():
        raise FieldValidationError(f"{label} cuma boleh huruf dan angka.")
    elif validation == "email" and not _EMAIL_RE.match(value):
        raise FieldValidationError(f"{label} harus email yang valid.")

    return value


def _char_is_emoji_ish(ch: str) -> bool:
    cp = ord(ch)
    if cp in (0x200D, 0xFE0F):  # zero-width joiner, variation selector
        return True
    if 0x1F3FB <= cp <= 0x1F3FF:  # skin tone modifiers
        return True
    if 0x1F1E6 <= cp <= 0x1F1FF:  # regional indicators (emoji bendera)
        return True
    try:
        category = unicodedata.category(ch)
    except (TypeError, ValueError):
        return False
    return category in ("So", "Sk")  # Symbol-other / Symbol-modifier: tempat emoji asli


def is_valid_emoji(value: str) -> bool:
    """Nerima emoji unicode asli (satuan atau gabungan ZWJ kayak emoji
    bendera atau gesture skin-tone) atau custom emoji Discord dalam bentuk
    <:name:id>/<a:name:id>. Dipake buat validasi parameter opsional `emoji`
    di /category create|edit. PartialEmoji.from_str() bawaan discord.py
    GAK validasi ini -- dia nganggep string apapun sebagai "unicode emoji"
    tanpa cek karakter beneran, jadi ini ngecek langsung lewat kategori
    Unicode-nya."""
    value = value.strip()
    if not value:
        return False
    if _CUSTOM_EMOJI_RE.match(value):
        return True
    if len(value) > 16:
        return False
    return all(_char_is_emoji_ish(ch) for ch in value)


def parse_hex_color(value: str | None, default: int) -> tuple[int | None, str | None]:
    """Parse kode warna hex (misal '#7C5CFF' atau '7C5CFF') jadi integer
    warna buat discord.Embed. Return (color, error_message) --
    error_message None kalau valid ATAU kalau `value` kosong (dalam kasus
    itu `default` yang dibalikin, dipake buat command yang warnanya
    opsional). Dipake bareng sama /iklan dan /welcome color biar aturan
    formatnya konsisten di semua tempat."""
    if not value:
        return default, None
    cleaned = value.strip()
    if not _HEX_COLOR_RE.match(cleaned):
        return None, "Warna harus kode hex 6 digit, misal `#7C5CFF` atau `7C5CFF`."
    return int(cleaned.lstrip("#"), 16), None
