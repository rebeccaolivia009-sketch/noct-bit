"""
NOCTRA leaderboard image generator v4 -- rombak total dari v3, fokusnya
bikin ini kerasa kayak KARTU MEMBER VIP, bukan poster e-sport:

  * Tipografi ganti dari DejaVu (font sistem generik) ke Poppins (bundled
    di bot/assets/fonts/) -- geometris, modern, jauh lebih "premium" buat
    judul & angka. DejaVu tetep jadi fallback kalau font Poppins-nya
    kelewat pas deploy (lihat _resolve_font_path).
  * Palet DISATUIN jadi satu keluarga warna hangat -- emas (#1), platinum
    hangat (#2), perunggu (#3) -- ganti dari crimson+silver-biru+bronze
    v3 yang tiga hue-nya gak nyambung satu sama lain. Ungu brand (ACCENT)
    disisain KHUSUS buat rank 4+ di list bawah, jadi podium (VIP) dan
    list biasa punya identitas visual yang beda tapi tetep senada.
  * Background disederhanain -- tekstur garis diagonal v3 dibuang abis
    (berisik, gak nambah apa-apa), ganti vignette halus + SATU glow emas
    lembut di belakang #1 (v3 pake 3 glow warna beda sekaligus, kesannya
    ramai). Sesuai arahan: niat & profesional, tapi jangan lebay.
  * Tiap podium card sekarang punya label kecil huruf kapital ("TOTAL
    BELANJA") di atas angka -- kesannya kayak statistik di kartu member,
    bukan cuma angka nempel doang.

Teknik font-bundling, no-emoji-policy, dan supersampling 2x tetep
dipertahanin sama persis kayak versi-versi sebelumnya.
"""

from __future__ import annotations

import math
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

# -- Palette -- satu keluarga warna hangat (emas/platinum/perunggu) +
# ungu brand buat rank 4+. Ganti total dari campuran crimson/silver-biru
# v3 yang kesannya rame/gak senada.
BG_TOP       = (9,   7,  15)
BG_BOT       = (19,  13, 28)
ACCENT       = (150, 122, 255)   # ungu brand -- KHUSUS rank 4+ di list
GOLD         = (216, 178, 96)    # #1
GOLD_SOFT    = (240, 214, 160)
PLATINUM     = (214, 206, 190)   # #2 -- platinum HANGAT, bukan biru kayak v3
BRONZE       = (198, 143, 96)    # #3
IVORY        = (250, 246, 238)   # putih hangat, bukan putih pucat
MUTED        = (172, 162, 182)
GLASS_TOP    = (255, 255, 255, 20)
GLASS_BOTTOM = (0,   0,   0,  50)
BAR_BG       = (0, 0, 0, 70)
MEDAL_CLR    = [GOLD, PLATINUM, BRONZE]

# -- Layout (nilai final pre-supersampling) ------------------------------------
IMG_W        = 1800
PAD          = 60
HEADER_H     = 230
PODIUM_H1    = 470
PODIUM_H23   = 356
PODIUM_EXTRA = 40
PODIUM_TO_LIST_GAP = 44
ROW_H        = 118
ROW_GAP      = 14
BOTTOM       = 48
RADIUS       = 26
AVATAR_D_1   = 112
AVATAR_D_23  = 86
AVATAR_D_LIST = 70
BADGE_D      = 52
BAR_W_FRAC   = 0.78

SS = 2

_ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_BOLD_CANDIDATES = [
    _ASSETS_DIR / "Poppins-Bold.ttf",
    _ASSETS_DIR / "DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]
_SEMIBOLD_CANDIDATES = [
    _ASSETS_DIR / "Poppins-SemiBold.ttf",
    *_BOLD_CANDIDATES,
]
_MEDIUM_CANDIDATES = [
    _ASSETS_DIR / "Poppins-Medium.ttf",
    _ASSETS_DIR / "DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
]
_REG_CANDIDATES = [
    _ASSETS_DIR / "Poppins-Regular.ttf",
    *_MEDIUM_CANDIDATES,
]


def _resolve_font_path(candidates: list) -> str | None:
    for candidate in candidates:
        if Path(candidate).is_file():
            return str(candidate)
    return None


_BOLD = _resolve_font_path(_BOLD_CANDIDATES)
_SEMIBOLD = _resolve_font_path(_SEMIBOLD_CANDIDATES)
_MEDIUM = _resolve_font_path(_MEDIUM_CANDIDATES)
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


def _draw_centered_tracked(draw, cx: int, y: int, text: str, font, fill, tracking: int = 0) -> None:
    w = _tracked_width(draw, text, font, tracking)
    _draw_tracked(draw, (cx - w // 2, y), text, font, fill, tracking)


def _gradient(w: int, h: int, top, bot) -> Image.Image:
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        r = int(top[0] * (1 - t) + bot[0] * t)
        g = int(top[1] * (1 - t) + bot[1] * t)
        b = int(top[2] * (1 - t) + bot[2] * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    return img


def _vignette(w: int, h: int, strength: int = 130) -> Image.Image:
    """Gelapin pinggiran kanvas dikit -- pengganti tekstur diagonal v3.
    Efeknya halus & gak berpola, jadi nambah kedalaman tanpa keliatan
    "rame" kayak garis-garis diagonal sebelumnya."""
    layer = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(layer)
    max_r = int(math.hypot(w, h) / 2)
    cx, cy = w // 2, int(h * 0.55)
    for r in range(max_r, 0, -14):
        a = int(strength * (1 - r / max_r) ** 3)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=min(255, a + (255 - strength)))
    layer = layer.point(lambda v: 255 - v)
    return Image.merge("RGBA", (Image.new("L", (w, h), 0), Image.new("L", (w, h), 0), Image.new("L", (w, h), 0), layer))


def _soft_glow(w: int, h: int, cx: int, cy: int, max_r: int, colour, peak_alpha: int) -> Image.Image:
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for r in range(max_r, 0, -10):
        a = int(peak_alpha * (r / max_r) ** 2.6)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*colour, a))
    return layer


def _glass_panel(
    img: Image.Image, x0: int, y0: int, x1: int, y1: int, radius: int,
    accent_top_colour=None, accent_top_h: int = 0, tint=None, tint_alpha: int = 26,
) -> None:
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=GLASS_TOP)
    img.alpha_composite(overlay)

    if tint is not None:
        tint_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        td = ImageDraw.Draw(tint_layer)
        td.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=(*tint, tint_alpha))
        img.alpha_composite(tint_layer)

    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([x0, y0 + (y1 - y0) // 2, x1, y1], radius=radius, fill=GLASS_BOTTOM)
    img.alpha_composite(shadow)

    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, outline=(255, 255, 255, 36), width=2)

    if accent_top_colour and accent_top_h:
        draw.rounded_rectangle([x0, y0, x1, y0 + accent_top_h], radius=radius // 2, fill=accent_top_colour)


def _circle_avatar(img: Image.Image, avatar: Image.Image | None, initials: str,
                    cx: int, top_y: int, d: int, ring_colour) -> None:
    x = cx - d // 2
    y = top_y
    draw = ImageDraw.Draw(img)
    if avatar:
        try:
            av = avatar.copy().convert("RGBA").resize((d, d), Image.LANCZOS)
            mask = Image.new("L", (d, d), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, d - 1, d - 1], fill=255)
            buf = Image.new("RGBA", (d, d))
            buf.paste(av, (0, 0))
            img.paste(buf, (x, y), mask)
            draw.ellipse([x - 4, y - 4, x + d + 3, y + d + 3], outline=ring_colour, width=4)
            return
        except Exception:
            pass
    draw.ellipse([x, y, x + d, y + d], fill=(46, 36, 62))
    draw.ellipse([x - 4, y - 4, x + d + 3, y + d + 3], outline=ring_colour, width=4)
    ini = (initials[:2] if len(initials) >= 2 else initials or "?").upper()
    f = _f(_SEMIBOLD, int(d * 0.34))
    fw = _tw(draw, ini, f)
    draw.text((x + (d - fw) // 2, y + (d - int(d * 0.4)) // 2), ini, font=f, fill=IVORY)


def _draw_crown(draw: ImageDraw.ImageDraw, cx: int, base_y: int, w: int, h: int, colour) -> None:
    hw = w // 2
    pts = [
        (cx - hw, base_y),
        (cx - hw, base_y - int(h * 0.42)),
        (cx - hw // 2, base_y - int(h * 0.75)),
        (cx - hw // 4, base_y - int(h * 0.42)),
        (cx, base_y - h),
        (cx + hw // 4, base_y - int(h * 0.42)),
        (cx + hw // 2, base_y - int(h * 0.75)),
        (cx + hw, base_y - int(h * 0.42)),
        (cx + hw, base_y),
    ]
    draw.polygon(pts, fill=colour, outline=IVORY)
    draw.rounded_rectangle([cx - hw, base_y - 6, cx + hw, base_y + 8], radius=4, fill=colour)
    jewel_r = max(3, int(h * 0.09))
    for jx, jy in ((cx - hw // 2, base_y - int(h * 0.75)), (cx, base_y - h), (cx + hw // 2, base_y - int(h * 0.75))):
        draw.ellipse([jx - jewel_r, jy - jewel_r, jx + jewel_r, jy + jewel_r], fill=IVORY)


def _draw_medal_badge(draw: ImageDraw.ImageDraw, cx: int, cy: int, rank: int, r: int, font) -> None:
    colour = MEDAL_CLR[rank] if rank < 3 else ACCENT
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colour, outline=IVORY, width=3)
    txt = str(rank + 1)
    tw_ = draw.textlength(txt, font=font)
    bbox = draw.textbbox((0, 0), txt, font=font)
    th = bbox[3] - bbox[1]
    draw.text((cx - tw_ // 2, cy - th // 2 - bbox[1]), txt, font=font, fill=(20, 14, 10))


def _fmt(amount: float, currency: str) -> str:
    c = currency.upper()
    if amount >= 1_000_000:
        s = f"{amount / 1_000_000:.1f}Jt"
    elif amount >= 1_000:
        s = f"{amount / 1_000:.1f}K"
    else:
        s = f"{amount:,.0f}"
    return f"{c} {s}"


def _draw_gradient_badge(
    img: Image.Image, cx: int, y: int, text: str, color_from: tuple, color_to: tuple, font, scale: int,
) -> int:
    """Gambar badge custom (teks + gradient 2 warna horizontal) di tengah
    (cx), nempel TEPAT di bawah nama -- CUMA dipake buat podium top 1-3
    yang punya badge kesimpen (lihat bot.database.queries.leaderboard.
    get_badge & bot.utils.leaderboard.refresh_leaderboard). Return tinggi
    total badge-nya (dipake caller buat geser elemen di bawahnya)."""
    draw = ImageDraw.Draw(img)
    tw_ = int(draw.textlength(text, font=font))
    pad_x = 16 * scale
    pad_y = 7 * scale
    badge_w = max(tw_ + pad_x * 2, 40 * scale)
    badge_h = _text_h(draw, font) + pad_y * 2
    x0 = cx - badge_w // 2

    grad = Image.new("RGB", (badge_w, badge_h))
    gd = ImageDraw.Draw(grad)
    for gx in range(badge_w):
        t = gx / max(1, badge_w - 1)
        r = int(color_from[0] * (1 - t) + color_to[0] * t)
        g = int(color_from[1] * (1 - t) + color_to[1] * t)
        b = int(color_from[2] * (1 - t) + color_to[2] * t)
        gd.line([(gx, 0), (gx, badge_h)], fill=(r, g, b))
    mask = Image.new("L", (badge_w, badge_h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, badge_w - 1, badge_h - 1], radius=badge_h // 2, fill=255)
    img.paste(grad, (x0, y), mask)

    bbox = draw.textbbox((0, 0), text, font=font)
    th = bbox[3] - bbox[1]
    draw = ImageDraw.Draw(img)
    draw.text((cx - tw_ // 2, y + (badge_h - th) // 2 - bbox[1]), text, font=font, fill=IVORY)
    return badge_h


def _podium_card(
    img: Image.Image, entry: dict, rank: int, x0: int, x1: int, top_y: int, bottom_y: int,
    avatar_d: int, max_spend: float,
) -> None:
    S = SS
    draw = ImageDraw.Draw(img)
    cx = (x0 + x1) // 2
    strip_colour = MEDAL_CLR[rank]

    _glass_panel(
        img, x0, top_y, x1, bottom_y, RADIUS * S,
        accent_top_colour=strip_colour, accent_top_h=8 * S,
        tint=strip_colour, tint_alpha=26,
    )
    draw = ImageDraw.Draw(img)

    avatar_top = top_y + 30 * S
    if rank == 0:
        _draw_crown(draw, cx, avatar_top - 10 * S, int(avatar_d * 0.82), int(avatar_d * 0.48), GOLD)
    _circle_avatar(img, entry.get("avatar"), entry.get("display_name", "?"), cx, avatar_top, avatar_d, MEDAL_CLR[rank])

    badge_cy = avatar_top + avatar_d
    _draw_medal_badge(ImageDraw.Draw(img), cx, badge_cy, rank, (BADGE_D * S) // 2, _f(_SEMIBOLD, 22 * S))

    draw = ImageDraw.Draw(img)
    name_size = 32 * S if rank == 0 else 24 * S
    f_name = _f(_SEMIBOLD, name_size)
    f_label = _f(_MEDIUM, 13 * S)
    amount_size = 36 * S if rank == 0 else 26 * S
    f_amount = _f(_BOLD, amount_size)
    f_orders = _f(_REG, 16 * S)

    name = entry.get("display_name", "Unknown")[:16]
    name_y = badge_cy + (BADGE_D * S) // 2 + 18 * S
    nw = _tw(draw, name, f_name)
    draw.text((cx - nw // 2, name_y), name, font=f_name, fill=IVORY)

    cursor_y = name_y + _text_h(draw, f_name) + 12 * S

    custom_badge = entry.get("badge")
    if custom_badge:
        badge_font = _f(_SEMIBOLD, 16 * S if rank == 0 else 14 * S)
        badge_h = _draw_gradient_badge(
            img, cx, cursor_y, custom_badge["text"][:24],
            custom_badge["color_from"], custom_badge["color_to"], badge_font, S,
        )
        draw = ImageDraw.Draw(img)
        cursor_y += badge_h + 12 * S

    # Label kecil huruf kapital di atas angka -- kesan "statistik kartu
    # member", bukan cuma angka nempel doang kayak v3.
    label_text = "TOTAL BELANJA"
    _draw_centered_tracked(draw, cx, cursor_y, label_text, f_label, MEDAL_CLR[rank], tracking=3 * S)
    cursor_y += _text_h(draw, f_label) + 8 * S

    spend = entry.get("total_spent", 0)
    spend_s = _fmt(spend, entry.get("currency_label", "IDR"))
    sw = _tw(draw, spend_s, f_amount)
    draw.text((cx - sw // 2, cursor_y), spend_s, font=f_amount, fill=IVORY)

    orders = entry.get("total_orders", 0)
    ord_txt = f"{orders} order{'s' if orders != 1 else ''}"
    ord_y = cursor_y + _text_h(draw, f_amount) + 12 * S
    ow = _tw(draw, ord_txt, f_orders)
    draw.text((cx - ow // 2, ord_y), ord_txt, font=f_orders, fill=MUTED)

    bar_w = int((x1 - x0) * BAR_W_FRAC)
    bar_x0 = cx - bar_w // 2
    bar_h = 12 * S
    bar_y = bottom_y - 30 * S - bar_h
    ratio = (spend / max_spend) if max_spend else 0
    fill_w = max(10 * S, int(bar_w * math.sqrt(max(0.0, min(1.0, ratio)))))
    draw.rounded_rectangle([bar_x0, bar_y, bar_x0 + bar_w, bar_y + bar_h], radius=bar_h // 2, fill=BAR_BG)
    draw.rounded_rectangle([bar_x0, bar_y, bar_x0 + fill_w, bar_y + bar_h], radius=bar_h // 2, fill=MEDAL_CLR[rank])


def generate_leaderboard_image(
    entries: list[dict],
    *,
    title: str = "NOCTRA STORE",
    subtitle: str = "TOP SPENDERS",
    timestamp: str = "",
    background: Image.Image | None = None,
) -> BytesIO:
    S = SS
    img_w = IMG_W * S
    pad = PAD * S
    header_h = HEADER_H * S
    podium_extra = PODIUM_EXTRA * S
    podium_h1 = PODIUM_H1 * S
    podium_h23 = PODIUM_H23 * S
    podium_to_list_gap = PODIUM_TO_LIST_GAP * S
    row_h = ROW_H * S
    row_gap = ROW_GAP * S
    bottom = BOTTOM * S
    radius = RADIUS * S
    avatar_d1 = AVATAR_D_1 * S
    avatar_d23 = AVATAR_D_23 * S
    avatar_d_list = AVATAR_D_LIST * S
    badge_d = BADGE_D * S

    top3 = entries[:3]
    rest = entries[3:]
    has_podium = len(top3) > 0
    max_spend = max((e.get("total_spent", 0) for e in top3), default=1) or 1

    podium_section_h = (podium_h1 + podium_extra) if has_podium else 0
    list_n = max(0, len(rest))
    list_section_h = list_n * (row_h + row_gap) - (row_gap if list_n else 0)

    h = header_h + podium_section_h + (podium_to_list_gap if (has_podium and list_n) else 0) + list_section_h + bottom
    h = max(h, header_h + bottom + 200 * S)

    if background is not None:
        # Background custom (logo/icon store, diatur staff lewat
        # /badge background) di-crop "cover" biar ngisi penuh kanvas tanpa
        # gepeng, terus dikasih gradient gelap semi-transparan DI ATASnya
        # -- tanpa ini, background yang terang bisa bikin nama/angka susah
        # kebaca.
        img = ImageOps.fit(background.convert("RGB"), (img_w, h), Image.LANCZOS).convert("RGBA")
        dark_overlay = _gradient(img_w, h, BG_TOP, BG_BOT).convert("RGBA")
        dark_overlay.putalpha(190)
        img.alpha_composite(dark_overlay)
    else:
        img = _gradient(img_w, h, BG_TOP, BG_BOT)
        img = img.convert("RGBA")
    img.alpha_composite(_vignette(img_w, h))
    if has_podium:
        # SATU glow emas lembut di belakang #1 -- v3 pake 3 glow warna
        # beda sekaligus (crimson+silver+bronze), kesannya rame. Di sini
        # sengaja cuma satu, biar mata fokus ke juara 1 doang.
        podium_cy = header_h + podium_section_h * 0.45
        img.alpha_composite(_soft_glow(img_w, h, img_w // 2, int(podium_cy), int(img_w * 0.42), GOLD, 22))
    draw = ImageDraw.Draw(img)

    f_title = _f(_BOLD, 54 * S)
    f_sub = _f(_SEMIBOLD, 20 * S)
    f_ts = _f(_REG, 15 * S)

    ty = 32 * S
    tw = _tw(draw, title, f_title)
    tx = (img_w - tw) // 2
    draw.text((tx + 2 * S, ty + 2 * S), title, font=f_title, fill=(0, 0, 0, 90))
    draw.text((tx, ty), title, font=f_title, fill=IVORY)

    sub_tracking = 8 * S
    sy = ty + 70 * S
    _draw_centered_tracked(draw, img_w // 2, sy, subtitle, f_sub, GOLD_SOFT, sub_tracking)

    # Divider kecil kiri-kanan subtitle, bukan garis lurus polos -- detail
    # kecil yang bikin kesan "lencana", tetep minimalis (gak lebay).
    div_gap = 14 * S
    sub_w = _tracked_width(draw, subtitle, f_sub, sub_tracking)
    div_y = sy + _text_h(draw, f_sub) // 2
    div_len = 46 * S
    draw.line([(img_w // 2 - sub_w // 2 - div_gap - div_len, div_y), (img_w // 2 - sub_w // 2 - div_gap, div_y)],
              fill=GOLD, width=2 * S)
    draw.line([(img_w // 2 + sub_w // 2 + div_gap, div_y), (img_w // 2 + sub_w // 2 + div_gap + div_len, div_y)],
              fill=GOLD, width=2 * S)

    if timestamp:
        ts_y = sy + 40 * S
        tsw = _tw(draw, timestamp, f_ts)
        draw.text(((img_w - tsw) // 2, ts_y), timestamp, font=f_ts, fill=MUTED)

    if has_podium:
        col_gap = 26 * S
        col_w = (img_w - 2 * pad - 2 * col_gap) // 3
        podium_bottom = header_h + podium_section_h - podium_extra // 2

        order_slots = [1, 0, 2]
        for slot_idx, entry_idx in enumerate(order_slots):
            if entry_idx >= len(top3):
                continue
            entry = top3[entry_idx]
            x0 = pad + slot_idx * (col_w + col_gap)
            x1 = x0 + col_w
            card_h = podium_h1 if entry_idx == 0 else podium_h23
            top_y = podium_bottom - card_h
            avatar_d = avatar_d1 if entry_idx == 0 else avatar_d23
            _podium_card(img, entry, entry_idx, x0, x1, top_y, podium_bottom, avatar_d, max_spend)

    if rest:
        f_rank_list = _f(_SEMIBOLD, 22 * S)
        f_name_list = _f(_SEMIBOLD, 24 * S)
        f_orders_list = _f(_REG, 16 * S)
        f_amount_list = _f(_BOLD, 24 * S)

        list_top = header_h + podium_section_h + (podium_to_list_gap if has_podium else 0)
        rx0 = pad
        rx1 = img_w - pad

        for i, entry in enumerate(rest):
            rank = i + 3
            ry0 = list_top + i * (row_h + row_gap)
            ry1 = ry0 + row_h

            _glass_panel(img, rx0, ry0, rx1, ry1, radius)
            draw = ImageDraw.Draw(img)

            badge_cx = rx0 + 50 * S
            badge_cy = ry0 + row_h // 2
            _draw_medal_badge(draw, badge_cx, badge_cy, rank, badge_d // 2, f_rank_list)

            av_x = badge_cx + badge_d // 2 + 24 * S
            av_y = ry0 + (row_h - avatar_d_list) // 2
            _circle_avatar(img, entry.get("avatar"), entry.get("display_name", "?"), av_x + avatar_d_list // 2, av_y, avatar_d_list, ACCENT)
            draw = ImageDraw.Draw(img)

            text_x = av_x + avatar_d_list + 26 * S
            name = entry.get("display_name", "Unknown")[:22]
            draw.text((text_x, ry0 + 24 * S), name, font=f_name_list, fill=IVORY)
            orders = entry.get("total_orders", 0)
            ord_txt = f"{orders} order{'s' if orders != 1 else ''}"
            draw.text((text_x, ry0 + 66 * S), ord_txt, font=f_orders_list, fill=MUTED)

            spend_s = _fmt(entry.get("total_spent", 0), entry.get("currency_label", "IDR"))
            sw2 = _tw(draw, spend_s, f_amount_list)
            draw.text((rx1 - 40 * S - sw2, ry0 + row_h // 2 - _text_h(draw, f_amount_list) // 2), spend_s, font=f_amount_list, fill=ACCENT)

    final_h = h // S
    img = img.convert("RGB").resize((IMG_W, final_h), Image.LANCZOS)

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf
