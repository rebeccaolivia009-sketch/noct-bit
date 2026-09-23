"""
NOCTRA invite tracker leaderboard image -- SENGAJA beda gaya total dari
bot.utils.leaderboard_image (Top Spenders): bukan podium 3 kolom +
glass-card, tapi LIST vertikal flat minimalis -- rank number raksasa
transparan sebagai dekorasi latar, garis tipis pemisah antar baris
(bukan card penuh berbayang), dan palet nyaris monokrom + satu warna
aksen ungu doang (gold/silver/bronze CUMA dipake buat #1-#3, sisanya
seragam). Tujuannya biar dua leaderboard yang bisa aktif bareng di
server yang sama keliatan jelas beda "identitas", bukan cuma re-skin
warna dari yang satunya.

Teknik font-bundling & no-emoji-policy tetep dipertahanin sama persis
kayak leaderboard_image.py -- DejaVu bundled, soalnya font lain gak
dijamin ada di image Railway/Nixpacks (lihat catatan di file itu).
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# -- Palette -- monokrom + satu aksen ungu. Beda total dari palet
# crimson/gold/silver/bronze-tint punya Top Spenders.
BG          = (9, 8, 14)
ACCENT      = (140, 112, 255)
RANK_TIER   = [(231, 181, 95), (198, 202, 214), (205, 140, 90)]  # #1 / #2 / #3 doang beda warna
WHITE       = (245, 244, 250)
MUTED       = (126, 120, 148)
LINE        = (255, 255, 255, 16)
GHOST_ALPHA = 12

IMG_W        = 1600
PAD_X        = 64
HEADER_H     = 176
ROW_H        = 104
ROW_GAP      = 6
BOTTOM       = 48
AVATAR_D     = 64
ACCENT_BAR_W = 4

SS = 2

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_BOLD_CANDIDATES = [
    _ASSETS_DIR / "DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]
_REG_CANDIDATES = [
    _ASSETS_DIR / "DejaVuSans.ttf",
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
        font = ImageFont.load_default()
    _font_cache[key] = font
    return font


def _tw(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    return int(draw.textlength(text, font=font))


def _text_h(draw, font) -> int:
    bbox = draw.textbbox((0, 0), "Ag", font=font)
    return bbox[3] - bbox[1]


def _tracked_width(draw, text: str, font, tracking: int) -> int:
    if not text:
        return 0
    return _tw(draw, text, font) + tracking * (len(text) - 1)


def _draw_tracked(draw, xy, text: str, font, fill, tracking: int = 0) -> None:
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += _tw(draw, ch, font) + tracking


def _circle_avatar(img: Image.Image, avatar: Image.Image | None, cx: int, cy: int, d: int, ring_colour) -> None:
    x, y = cx - d // 2, cy - d // 2
    draw = ImageDraw.Draw(img)
    ok = False
    if avatar:
        try:
            av = avatar.copy().convert("RGBA").resize((d, d), Image.LANCZOS)
            mask = Image.new("L", (d, d), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, d - 1, d - 1], fill=255)
            buf = Image.new("RGBA", (d, d))
            buf.paste(av, (0, 0))
            img.paste(buf, (x, y), mask)
            ok = True
        except Exception:
            ok = False
    if not ok:
        draw.ellipse([x, y, x + d, y + d], fill=(38, 34, 54))
    draw.ellipse([x, y, x + d, y + d], outline=ring_colour, width=2)


def generate_invite_leaderboard_image(
    entries: list[dict],
    *,
    title: str = "INVITE TRACKER",
    subtitle: str = "TOP INVITERS",
    timestamp: str = "",
) -> BytesIO:
    """`entries` -- list of dict {rank, display_name, total_invites, avatar
    (PIL Image | None)}, urutan udah rank-sorted dari caller
    (bot.utils.invite_tracker.refresh_invite_leaderboard)."""
    S = SS
    img_w = IMG_W * S
    pad_x = PAD_X * S
    header_h = HEADER_H * S
    row_h = ROW_H * S
    row_gap = ROW_GAP * S
    bottom = BOTTOM * S
    avatar_d = AVATAR_D * S
    accent_bar_w = ACCENT_BAR_W * S

    n = len(entries)
    h = header_h + n * (row_h + row_gap) - (row_gap if n else 0) + bottom
    h = max(h, header_h + bottom + 200 * S)

    img = Image.new("RGB", (img_w, h), BG).convert("RGBA")
    draw = ImageDraw.Draw(img)

    f_eyebrow = _f(_BOLD, 20 * S)
    f_title = _f(_BOLD, 50 * S)
    f_ts = _f(_REG, 15 * S)

    ex, ey = pad_x, 40 * S
    _draw_tracked(draw, (ex, ey), subtitle, f_eyebrow, ACCENT, tracking=6 * S)

    ty = ey + 32 * S
    draw.text((ex, ty), title, font=f_title, fill=WHITE)

    if timestamp:
        tsw = _tw(draw, timestamp, f_ts)
        draw.text((img_w - pad_x - tsw, ty + 16 * S), timestamp, font=f_ts, fill=MUTED)

    line_y = header_h - 24 * S
    draw.line([(pad_x, line_y), (img_w - pad_x, line_y)], fill=LINE, width=1 * S)

    f_rank_ghost = _f(_BOLD, 76 * S)
    f_name = _f(_BOLD, 27 * S)
    f_sub = _f(_REG, 16 * S)
    f_number = _f(_BOLD, 34 * S)
    f_unit = _f(_REG, 15 * S)

    row_x0, row_x1 = pad_x, img_w - pad_x

    for i, entry in enumerate(entries):
        ry0 = header_h + i * (row_h + row_gap)
        ry1 = ry0 + row_h
        rank = entry.get("rank", i)
        tier_colour = RANK_TIER[rank] if rank < 3 else ACCENT

        if i > 0:
            draw.line([(row_x0, ry0), (row_x1, ry0)], fill=LINE, width=1 * S)

        draw.rectangle(
            [row_x0, ry0 + 10 * S, row_x0 + accent_bar_w, ry1 - 10 * S], fill=tier_colour
        )

        # Nomor rank raksasa transparan di belakang -- dekoratif doang,
        # gak ganggu keterbacaan avatar/nama/angka di depannya.
        ghost = f"{rank + 1:02d}"
        draw.text(
            (row_x0 + 24 * S, ry0 + (row_h - _text_h(draw, f_rank_ghost)) // 2 - 6 * S),
            ghost, font=f_rank_ghost, fill=(255, 255, 255, GHOST_ALPHA),
        )

        av_cx = row_x0 + 150 * S
        av_cy = ry0 + row_h // 2
        _circle_avatar(img, entry.get("avatar"), av_cx, av_cy, avatar_d, tier_colour)
        draw = ImageDraw.Draw(img)

        text_x = av_cx + avatar_d // 2 + 28 * S
        name = entry.get("display_name", "Unknown")[:26]
        draw.text((text_x, ry0 + row_h // 2 - 24 * S), name, font=f_name, fill=WHITE)
        draw.text((text_x, ry0 + row_h // 2 + 6 * S), f"Peringkat #{rank + 1}", font=f_sub, fill=MUTED)

        total = entry.get("total_invites", 0)
        number_s = str(total)
        unit_s = "orang"
        nw = _tw(draw, number_s, f_number)
        uw = _tw(draw, unit_s, f_unit)
        draw.text((row_x1 - max(nw, uw), ry0 + row_h // 2 - 22 * S), number_s, font=f_number, fill=tier_colour)
        draw.text((row_x1 - uw, ry0 + row_h // 2 + 6 * S), unit_s, font=f_unit, fill=MUTED)

    final_h = h // S
    img = img.convert("RGB").resize((IMG_W, final_h), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
