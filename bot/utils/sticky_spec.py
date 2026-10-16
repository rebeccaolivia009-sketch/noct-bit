"""
Logika murni (tanpa Discord/DB) buat Sticky Message: nilai default, form
modal, parser, validasi, dan diff "sebelum -> sesudah". Dipakai panel
(bot.ui.sticky) dan dites sendiri.

Susunan pesan sticky (dari atas ke bawah):
    Judul  ->  pemisah  ->  deskripsi  ->  pemisah  ->  thumbnail (+ footer)
"""

from __future__ import annotations

import re

from bot.utils.builder_spec import InputSpec, valid_url

MAX_TITLE = 100
MAX_DESCRIPTION = 3000   # total teks per pesan V2 maks 4000 -- sisain buat judul & footer
MAX_FOOTER = 100
MAX_URL = 500

DEFAULT_COLOR = 0x7C5CFF   # sama dengan COLOR_ACCENT tema

FORMS: dict[str, tuple[InputSpec, ...]] = {
    "text": (
        InputSpec("title", "Judul", required=True, max_length=MAX_TITLE,
                  placeholder="Contoh: Aturan Channel"),
        InputSpec("description", "Deskripsi", style="paragraph", required=True,
                  max_length=MAX_DESCRIPTION,
                  placeholder="Bisa pakai markdown: **tebal**, *miring*, daftar, > kutipan"),
        InputSpec("footer_text", "Teks bawah (di samping thumbnail)", max_length=MAX_FOOTER,
                  placeholder="Opsional"),
    ),
    "look": (
        InputSpec("thumbnail_url", "URL thumbnail (gambar kecil di bawah)", parser="url",
                  max_length=MAX_URL, placeholder="https://... (PNG/JPG/WebP, opsional)"),
        InputSpec("accent_color", "Warna garis samping (hex)", max_length=7,
                  placeholder="#7C5CFF -- kosong = warna default"),
    ),
}
FORM_TITLE = {"text": "Sticky: Judul & Teks", "look": "Sticky: Tampilan"}

KEY_LABEL = {
    "title": "Judul", "description": "Deskripsi", "footer_text": "Teks bawah",
    "thumbnail_url": "Thumbnail", "accent_color": "Warna", "enabled": "Status",
}

_HEX = re.compile(r"^#?[0-9a-fA-F]{6}$")


def new_values() -> dict:
    return {
        "title": None, "description": None, "footer_text": None,
        "thumbnail_url": None, "accent_color": None, "enabled": 1,
    }


def values_from_row(row) -> dict:
    return {k: row[k] for k in new_values()}


def color_text(color: int | None) -> str:
    return "" if color is None else f"#{int(color):06X}"


def form_defaults(form: str, values: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for spec in FORMS[form]:
        value = values.get(spec.key)
        if spec.key == "accent_color":
            out[spec.key] = color_text(value)
        else:
            out[spec.key] = "" if value is None else str(value)
    return out


def apply_form(form: str, raw: dict[str, str]) -> tuple[dict, list[str]]:
    """Jawaban modal -> (nilai valid buat draft, daftar error). Field yang
    error gak ikut diterapin (nilai lamanya dipertahankan)."""
    updates: dict = {}
    errors: list[str] = []
    for spec in FORMS[form]:
        text = (raw.get(spec.key) or "").strip()
        if not text:
            if spec.required:
                errors.append(f"{spec.label} wajib diisi.")
            else:
                updates[spec.key] = None
            continue
        if spec.key == "accent_color":
            if not _HEX.match(text):
                errors.append("Warna harus kode hex 6 digit, misal `#7C5CFF`.")
                continue
            updates[spec.key] = int(text.lstrip("#"), 16)
        elif spec.parser == "url":
            if not valid_url(text):
                errors.append(f"{spec.label}: harus link http(s) yang valid (tanpa spasi).")
                continue
            updates[spec.key] = text
        else:
            updates[spec.key] = text
    return updates, errors


def validate(values: dict) -> list[str]:
    """Masalah yang ngeblok Simpan (kosong = boleh)."""
    problems: list[str] = []
    title = (values.get("title") or "").strip()
    description = (values.get("description") or "").strip()
    if not title:
        problems.append("Judul belum diisi.")
    elif len(title) > MAX_TITLE:
        problems.append(f"Judul maksimal {MAX_TITLE} karakter.")
    if not description:
        problems.append("Deskripsi belum diisi.")
    elif len(description) > MAX_DESCRIPTION:
        problems.append(f"Deskripsi maksimal {MAX_DESCRIPTION} karakter.")
    if len((values.get("footer_text") or "")) > MAX_FOOTER:
        problems.append(f"Teks bawah maksimal {MAX_FOOTER} karakter.")
    thumb = (values.get("thumbnail_url") or "").strip()
    if thumb and not valid_url(thumb):
        problems.append("URL thumbnail gak valid.")
    return problems


def _norm(value):
    if value in ("", None):
        return None
    if isinstance(value, bool):
        return int(value)
    return value


def changed_keys(original: dict | None, values: dict) -> list[str]:
    if original is None:
        return []
    return [k for k in new_values() if _norm(values.get(k)) != _norm(original.get(k))]


def _short(value, limit: int = 50) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def fmt(key: str, value) -> str:
    if key == "enabled":
        return "aktif" if value else "nonaktif"
    if key == "accent_color":
        return color_text(value) or "(default)"
    if value in (None, ""):
        return "-"
    return _short(value)


def diff(original: dict | None, values: dict) -> list[str]:
    lines = []
    for key in changed_keys(original, values):
        lines.append(
            f"\u2022 **{KEY_LABEL[key]}**: `{fmt(key, (original or {}).get(key))}` \u2192 `{fmt(key, values.get(key))}`"
        )
    return lines
