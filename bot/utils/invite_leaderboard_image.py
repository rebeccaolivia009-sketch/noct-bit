"""
NOCTRA invite tracker leaderboard image -- v2.

Beda dari v1: rank number sekarang badge SOLID (rounded square, angka
di-center presisi lewat textbbox, bukan digit raksasa transparan yang
gampang numbuk avatar), tiap baris dikasih progress bar tipis (proporsi
invite user itu dibanding #1 -- sekalian ngisi ruang vertikal biar gak
kosong melompong), dan tinggi image sekarang ngikutin JUMLAH BARIS
BENERAN (gak ada padding minimum paksa yang bikin banyak ruang kosong
pas entry-nya dikit).

Masih beda gaya total dari bot.utils.leaderboard_image (podium + glass
card Top Spenders) -- di sini tetep list flat minimalis, palet nyaris
monokrom + satu aksen ungu, gold/silver/bronze cuma buat #1-#3.

Teknik font-bundling & no-emoji-policy tetep dipertahanin sama persis
kayak leaderboard_image.py -- DejaVu bundled, soalnya font lain gak
dijamin ada di image Railway/Nixpacks.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# -- Palette -------------------------------------------------------------
BG          = (9, 8, 14)
ACCENT      = (140, 112, 255)
RANK_TIER   = [(231, 181, 95), (198, 202, 214), (205, 140, 90)]  # #1 / #2 / #3
RANK_BADGE_TEXT_DARK = (18, 16, 26)   # dipake di atas badge solid #1-#3 (terang)
WHITE       = (245, 244, 250)
MUTED       = (126, 120, 148)

# PIL's ImageDraw gak nge-alpha-blend pas nge-fill langsung ke canvas
# (beda sama Image.alpha_composite) -- warna RGBA transparan kepake APA
# ADANYA terus ke-drop pas final convert("RGB"), hasilnya putih SOLID,
# bukan abu-abu tipis kayak yang diniatin (ini bug v1 -- progress bar &
# outline badge #4 ke-render putih terang, bukan subtle). Makanya di sini
# semua "warna transparan" udah di-blend MANUAL ke BG jadi RGB solid.
def _blend(fg, alpha_255: int, bg=BG):
    a = alpha_255 / 255
    return tuple(round(f * a + b * (1 - a)) for f, b in zip(fg, bg))


LINE       = _blend((255, 255, 255), 16)
TRACK_BG   = _blend((255, 255, 255), 26)
BADGE_RING = _blend((255, 255, 255), 70)

IMG_W        = 1600
PAD_X        = 64
HEADER_H     = 168
ROW_H        = 118
ROW_GAP      = 4
TOP_PAD      = 20
BOTTOM_PAD   = 44
AVATAR_D     = 60
BADGE_D      = 52
EMPTY_H      = 260

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


def _draw_tracked(draw, xy, text: str, font, fill, tracking: int = 0) -> None:
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += _tw(draw, ch, font) + tracking


def _draw_centered(draw, cx: int, cy: int, text: str, font, fill) -> None:
    """Center teks presisi pake textbbox (bukan textlength doang), biar
    angka di badge rank gak geser-geser dikit tergantung digit/font
    metrics -- ini yang bikin v1 keliatan 'gak beraturan'."""
    bbox = draw.textbbox((0, 0), text, font=font)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((cx - w / 2 - bbox[0], cy - h / 2 - bbox[1]), text, font=font, fill=fill)


def _rounded_rect(draw, box, radius, fill=None, outline=None, width=1) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


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
    (PIL Image | None)}, urutan udah rank-sorted dari caller."""
    S = SS
    img_w = IMG_W * S
    pad_x = PAD_X * S
    header_h = HEADER_H * S
    row_h = ROW_H * S
    row_gap = ROW_GAP * S
    top_pad = TOP_PAD * S
    bottom_pad = BOTTOM_PAD * S
    avatar_d = AVATAR_D * S
    badge_d = BADGE_D * S

    n = len(entries)
    body_h = (n * (row_h + row_gap) - row_gap) if n else EMPTY_H * S
    h = header_h + top_pad + body_h + bottom_pad

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

    line_y = header_h - 20 * S
    draw.line([(pad_x, line_y), (img_w - pad_x, line_y)], fill=LINE, width=1 * S)

    if not entries:
        f_empty = _f(_REG, 20 * S)
        msg = "Belum ada invite yang tercatat."
        mw = _tw(draw, msg, f_empty)
        draw.text(((img_w - mw) / 2, header_h + top_pad + body_h / 2 - 12 * S), msg, font=f_empty, fill=MUTED)
        final_h = h // S
        img = img.convert("RGB").resize((IMG_W, final_h), Image.LANCZOS)
        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.seek(0)
        return buf

    f_badge = _f(_BOLD, 22 * S)
    f_name = _f(_BOLD, 27 * S)
    f_number = _f(_BOLD, 32 * S)
    f_unit = _f(_REG, 15 * S)

    row_x0, row_x1 = pad_x, img_w - pad_x
    top_total = max((e.get("total_invites", 0) for e in entries), default=0) or 1

    for i, entry in enumerate(entries):
        ry0 = header_h + top_pad + i * (row_h + row_gap)
        ry1 = ry0 + row_h
        cy = ry0 + row_h // 2
        rank = entry.get("rank", i)
        tier_colour = RANK_TIER[rank] if rank < 3 else None

        if i > 0:
            draw.line([(row_x0, ry0), (row_x1, ry0)], fill=LINE, width=1 * S)

        # -- Rank badge: solid buat #1-#3, outline tipis buat sisanya --
        badge_x0 = row_x0
        badge_y0 = cy - badge_d // 2
        badge_box = [badge_x0, badge_y0, badge_x0 + badge_d, badge_y0 + badge_d]
        if tier_colour:
            _rounded_rect(draw, badge_box, radius=14 * S, fill=tier_colour)
            _draw_centered(draw, badge_x0 + badge_d // 2, cy, str(rank + 1), f_badge, RANK_BADGE_TEXT_DARK)
        else:
            _rounded_rect(draw, badge_box, radius=14 * S, outline=BADGE_RING, width=2 * S)
            _draw_centered(draw, badge_x0 + badge_d // 2, cy, str(rank + 1), f_badge, MUTED)

        av_cx = badge_x0 + badge_d + 34 * S + avatar_d // 2
        _circle_avatar(img, entry.get("avatar"), av_cx, cy, avatar_d, tier_colour or ACCENT)
        draw = ImageDraw.Draw(img)

        text_x = av_cx + avatar_d // 2 + 26 * S
        name = entry.get("display_name", "Unknown")[:26]
        draw.text((text_x, cy - row_h * 0.30), name, font=f_name, fill=WHITE)

        # -- Progress bar tipis: proporsi total invite user ini vs #1,
        # sekalian ngisi ruang di bawah nama biar baris gak keliatan
        # kosong pas entry dikit.
        bar_y = cy + row_h * 0.14
        bar_w = min(320 * S, row_x1 - text_x - 260 * S)
        bar_w = max(bar_w, 80 * S)
        bar_h = 8 * S
        total = entry.get("total_invites", 0)
        frac = min(1.0, total / top_total) if top_total else 0
        _rounded_rect(draw, [text_x, bar_y, text_x + bar_w, bar_y + bar_h], radius=bar_h // 2, fill=TRACK_BG)
        if frac > 0:
            fill_w = max(bar_h, bar_w * frac)
            _rounded_rect(
                draw, [text_x, bar_y, text_x + fill_w, bar_y + bar_h],
                radius=bar_h // 2, fill=tier_colour or ACCENT,
            )

        number_s = str(total)
        unit_s = "orang"
        nw = _tw(draw, number_s, f_number)
        uw = _tw(draw, unit_s, f_unit)
        col_w = max(nw, uw)
        draw.text((row_x1 - col_w + (col_w - nw) / 2, cy - 26 * S), number_s, font=f_number, fill=tier_colour or ACCENT)
        draw.text((row_x1 - col_w + (col_w - uw) / 2, cy + 8 * S), unit_s, font=f_unit, fill=MUTED)

    final_h = h // S
    img = img.convert("RGB").resize((IMG_W, final_h), Image.LANCZOS)
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
