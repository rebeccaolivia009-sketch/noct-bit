"""
NOCTRA visual theme.

Discord embeds cannot render SVG -- the platform only displays raster images
(PNG/JPG/GIF/WebP) and has no inline icon system for embed text. This module
defines the dark purple / blue violet colour palette plus a small set of
plain typographic markers (not emoji) used in place of icons, so the bot
keeps a consistent, premium, minimal look using only what Discord actually
renders.

Wherever a real icon/badge is wanted (product thumbnails, ticket headers,
banners), pass an `image_url` / `thumbnail_url` pointing at a hosted PNG or
WebP exported from your SVG source -- every embed builder in `bot/ui/embeds.py`
accepts one.
"""

from __future__ import annotations

BRAND_NAME = "NOCTRA"

# -- Palette -----------------------------------------------------------------
COLOR_PRIMARY = 0x4B1FA8     # deep purple
COLOR_ACCENT = 0x7C5CFF      # blue violet
COLOR_SUCCESS = 0x2ECC71
COLOR_WARNING = 0xF5A623
COLOR_DANGER = 0xE74C3C
COLOR_MUTED = 0x2B2640        # near-black violet, used for neutral/info embeds

STATUS_COLORS = {
    "pending": COLOR_WARNING,
    "processing": COLOR_ACCENT,
    "completed": COLOR_SUCCESS,
    "cancelled": COLOR_MUTED,
    "refunded": COLOR_DANGER,
    "paid": COLOR_SUCCESS,
    "expired": COLOR_DANGER,
    "open": COLOR_ACCENT,
    "closed": COLOR_MUTED,
    "archived": COLOR_MUTED,
    "approved": COLOR_SUCCESS,
    "rejected": COLOR_DANGER,
    "hidden": COLOR_MUTED,
}

# -- Typographic markers (NOT emoji) -----------------------------------------
MARK_BULLET = "▸"
MARK_DIAMOND = "◆"
MARK_DASH = "—"
MARK_BLOCK_FULL = "█"
MARK_BLOCK_EMPTY = "░"
MARK_CHECK = "✓"
MARK_CROSS = "✕"

FOOTER_TEXT = f"{BRAND_NAME} STORE"


def rating_bar(average: float, scale: int = 10) -> str:
    """Render a simple block-character bar for a 0-5 rating. Kept around
    for the per-star distribution histogram (rating_distribution_embed),
    which renders its own bars inline -- for a single average rating,
    prefer star_rating() below instead, since the solid block characters
    render as a plain flat bar (no visible texture) inside a Components V2
    TextDisplay."""
    filled = round((average / 5) * scale) if average else 0
    filled = max(0, min(scale, filled))
    return MARK_BLOCK_FULL * filled + MARK_BLOCK_EMPTY * (scale - filled)


def star_rating(average: float, scale: int = 5) -> str:
    """Baris bintang (⭐/☆) buat nampilin rating rata-rata, dibulatin ke
    bintang penuh terdekat -- dipake di kartu produk (Components V2) dan
    review card. `average` bisa desimal (misal 4.5); presisi aslinya tetep
    ditampilin terpisah sebagai teks angka (misal "4.5/5"), bintangnya cuma
    representasi visual yang dibulatin."""
    filled = round(average) if average else 0
    filled = max(0, min(scale, filled))
    return "\u2b50" * filled + "\u2606" * (scale - filled)
