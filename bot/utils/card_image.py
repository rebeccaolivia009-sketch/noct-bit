"""
NOCTRA digital card image generator -- v2. Background sekarang PAKE ASSET
GAMBAR STATIS yang Nikss desain sendiri (bot/assets/images/card_background.png),
GANTI dari versi awal yang generate seluruh background (gradient/chip/dots)
lewat PIL primitives. Bot cuma nempelin bagian yang DINAMIS di atasnya:
teks Credit/Noctoins/Server Points, avatar bulat, dan username.

Font tetep dibundle dari bot/assets/fonts/ (sama alasannya kayak
bot.utils.leaderboard_image -- gak ada font system di Railway/Nixpacks,
Pillow diem-diem fallback ke bitmap font kalau font-nya gak ke-load).

PENTING soal koordinat: STAT_ROWS/AVATAR_* di bawah ini didapet dari
analisis PIXEL background.png yang dikirim Nikss (posisi vertikal tiap
icon, dll) -- BUKAN dihitung otomatis dari gambar tiap kali generate.
Kalau background-nya diganti sama versi baru yang tata letaknya beda,
koordinat ini WAJIB disesuain ulang manual di sini.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WHITE  = (245, 243, 250)
ACCENT = (124, 92, 255)   # COLOR_ACCENT, dipake buat ring avatar
PRIMARY = (75, 31, 168)   # COLOR_PRIMARY, dipake buat fallback avatar kosong

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"
_BG_PATH = _ASSETS_DIR / "images" / "card_background.png"

_BOLD_CANDIDATES = [
    _ASSETS_DIR / "fonts" / "DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]
_REG_CANDIDATES = [
    _ASSETS_DIR / "fonts" / "DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]


def _resolve_font_path(candidates: list) -> str | None:
    for candidate in candidates:
        if Path(candidate).is_file():
            return str(candidate)
    return None


_BOLD = _resolve_font_path(_BOLD_CANDIDATES)
_REG = _resolve_font_path(_REG_CANDIDATES)

_font_cache: dict[tuple[str | None, int], ImageFont.FreeTypeFont] = {}


def _f(path: str | None, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    if key in _font_cache:
        return _font_cache[key]
    font = None
    if path:
        try:
            font = ImageFont.truetype(path, size)
        except Exception:
            font = None
    if font is None:
        # Last-resort fallback -- ignores `size`. Kalau ini ke-trigger,
        # folder bot/assets/fonts gak ke-deploy bareng kode.
        font = ImageFont.load_default()
    _font_cache[key] = font
    return font


# -- Koordinat, hasil analisis pixel bot/assets/images/card_background.png --
# (kind, y tengah icon) -- x kolom icon berakhir di x=196, teks mulai abis itu.
STAT_ROWS = [
    ("Credits", 358),
    ("Noctoins", 500),
    ("Server Points", 635),
]
STAT_TEXT_X = 232
FONT_SIZE_STAT = 42

AVATAR_X = 100
AVATAR_Y = 770
AVATAR_D = 140
USERNAME_FONT_SIZE = 39


def _circle_avatar(base: Image.Image, avatar: Image.Image | None, initials: str,
                    x: int, y: int, d: int) -> None:
    draw = ImageDraw.Draw(base)
    if avatar:
        try:
            av = avatar.copy().convert("RGBA").resize((d, d), Image.LANCZOS)
            mask = Image.new("L", (d, d), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, d - 1, d - 1], fill=255)
            buf = Image.new("RGBA", (d, d))
            buf.paste(av, (0, 0))
            base.paste(buf, (x, y), mask)
            draw.ellipse([x - 3, y - 3, x + d + 2, y + d + 2], outline=ACCENT, width=3)
            return
        except Exception:
            pass
    draw.ellipse([x, y, x + d, y + d], fill=PRIMARY)
    draw.ellipse([x - 3, y - 3, x + d + 2, y + d + 2], outline=ACCENT, width=3)
    ini = (initials[:2] if len(initials) >= 2 else initials or "?").upper()
    f = _f(_BOLD, int(d * 0.34))
    draw2 = ImageDraw.Draw(base)
    fw = int(draw2.textlength(ini, font=f))
    draw2.text((x + (d - fw) // 2, y + int(d * 0.3)), ini, font=f, fill=WHITE)


def generate_card_image(
    *,
    username: str,
    avatar: Image.Image | None,
    credit_balance: float,
    currency_label: str,
    noctoins: int,
    server_points: int,
    tier: str = "standard",
) -> BytesIO:
    """`tier` gak dipake lagi di sini -- label "standard" udah baked-in
    langsung di background.png (statis), bukan dirender dinamis. Kalau
    nanti butuh tier yang beda-beda per kartu, itu perlu background
    terpisah per tier atau nge-cover area itu manual -- bilang aja kalau
    itu perlu."""
    if not _BG_PATH.is_file():
        raise FileNotFoundError(
            f"Card background gak ketemu di {_BG_PATH} -- pastiin file-nya ke-deploy bareng kode."
        )

    img = Image.open(_BG_PATH).convert("RGBA")
    draw = ImageDraw.Draw(img)

    f_stat = _f(_REG, FONT_SIZE_STAT)
    values = [
        f"{credit_balance:,.0f} {currency_label}",
        f"{noctoins} Noctoins",
        f"{server_points} Server Points",
    ]
    for (label, icon_cy), value in zip(STAT_ROWS, values):
        bbox = draw.textbbox((0, 0), value, font=f_stat)
        text_h = bbox[3] - bbox[1]
        draw.text((STAT_TEXT_X, icon_cy - text_h // 2 - bbox[1]), value, font=f_stat, fill=WHITE)

    _circle_avatar(img, avatar, username, AVATAR_X, AVATAR_Y, AVATAR_D)
    draw = ImageDraw.Draw(img)  # re-acquire abis paste ops
    f_user = _f(_REG, USERNAME_FONT_SIZE)
    bbox = draw.textbbox((0, 0), username[:24], font=f_user)
    text_h = bbox[3] - bbox[1]
    draw.text(
        (AVATAR_X + AVATAR_D + 28, AVATAR_Y + AVATAR_D // 2 - text_h // 2 - bbox[1]),
        username[:24], font=f_user, fill=WHITE,
    )

    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
