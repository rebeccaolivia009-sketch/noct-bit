"""
Gaya kartu Statistik Pembelian (/stats): level visual, teks, dan format --
fungsi murni (tanpa Discord/DB) biar gampang dites.

Semakin tinggi belanja, semakin UNIK kartunya. Ada 6 level + 3 gaya
juara, dan tiap gaya beda di LIMA hal: warna aksen, emoji & judul,
ornamen bingkai, kalimat penutup, dan INFO EKSKLUSIF yang kebuka:

    L0  Pendatang   -- belum ada pesanan selesai
    L1  Bronze      -- info dasar
    L2  Silver      -- + member sejak
    L3  Gold        -- + produk favorit
    L4  Diamond     -- + rata-rata & pesanan terbesar
    L5  Obsidian    -- + pesanan terakhir
    C1  Hall of Fame (peringkat 1)  -- semua + badge custom + selisih unggul
    C2  Elite Runner-Up (peringkat 2) -- semua + badge custom
    C3  Elite Top 3 (peringkat 3)     -- semua + badge custom

Level L1-L5 ditentukan dari TIER loyalitas yang diatur staff (/tier): makin
tinggi tier yang kecapai, makin tinggi levelnya, dan tier teratas SELALU
dapet L5 berapa pun jumlah tier-nya. Kalau belum ada tier yang diatur,
dipake persentil peringkat. Top 3 leaderboard selalu dapet gaya juara.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from bot.utils.helpers import format_price

LEVELS = 5


@dataclass
class CardData:
    user_id: int
    name: str
    avatar_url: str | None = None
    spend: float = 0.0
    orders: int = 0
    rank: int | None = None
    total_spenders: int = 0
    gap_to_next: float | None = None       # selisih belanja ke peringkat di atasnya
    currency: str = "IDR"
    tier_name: str | None = None
    next_tier_name: str | None = None
    next_remaining: float | None = None
    progress: float | None = None          # 0..1 menuju tier berikutnya
    level: int = 0
    crown: int | None = None               # 1/2/3 kalau top 3
    first_order_at: datetime | None = None
    last_order_at: datetime | None = None
    favorite_name: str | None = None
    favorite_qty: int = 0
    biggest: float = 0.0
    badge_text: str | None = None
    lead_over_second: float | None = None  # cuma buat peringkat 1
    excluded: bool = False                 # akun dikecualiin dari leaderboard

    @property
    def average(self) -> float:
        return self.spend / self.orders if self.orders else 0.0


@dataclass(frozen=True)
class Style:
    key: str
    accent: int
    emoji: str
    title: str
    label: str
    frame: str
    flair: str
    perks: tuple[str, ...]


_BASE_PERKS = ("since", "favorite", "average", "biggest", "last")

STYLES: dict[str, Style] = {
    "L0": Style("L0", 0x6B7280, "\U0001F4CA", "Statistik Pembelian", "PENDATANG BARU", "",
                "Belum ada pesanan selesai \u2014 pesanan pertamamu bakal tampil di sini.", ()),
    "L1": Style("L1", 0xCD7F32, "\U0001F949", "Kartu Bronze", "BRONZE CARD",
                "\u2500\u2500\u2500 \U0001F949 \u2500\u2500\u2500",
                "Perjalananmu di NOCTRA baru dimulai.", ()),
    "L2": Style("L2", 0xC0C8D4, "\U0001F948", "Kartu Silver", "SILVER CARD",
                "\u2550\u2550 \U0001F948 \u2550\u2550",
                "Pelanggan yang mulai dikenal.", ("since",)),
    "L3": Style("L3", 0xF5B301, "\U0001F947", "Kartu Gold", "GOLD CARD",
                "\u2726 \u2500\u2500\u2500 \U0001F947 \u2500\u2500\u2500 \u2726",
                "Langganan setia yang bersinar.", ("since", "favorite")),
    "L4": Style("L4", 0x4DD0E1, "\U0001F48E", "Kartu Diamond", "DIAMOND CARD",
                "\u25C6 \u2501\u2501\u2501 \U0001F48E \u2501\u2501\u2501 \u25C6",
                "Level belanja yang jarang dicapai.", ("since", "favorite", "average", "biggest")),
    "L5": Style("L5", 0x9B5CFF, "\U0001F52E", "Kartu Obsidian", "OBSIDIAN CARD \u00B7 MYTHIC",
                "\u2727 \u2550\u2550\u2550 \u2726 \U0001F52E \u2726 \u2550\u2550\u2550 \u2727",
                "Nama yang tercatat di sejarah NOCTRA.", _BASE_PERKS),
    "C1": Style("C1", 0xFFD700, "\U0001F451", "Hall of Fame", "\U0001F451 NOCTRA CHAMPION \u00B7 PERINGKAT 1",
                "\U0001F451 \u2550\u2550\u2550\u2550\u2550\u2550\u2550 \u2726 \u2550\u2550\u2550\u2550\u2550\u2550\u2550 \U0001F451",
                "Takhta tertinggi toko ada di tanganmu.", _BASE_PERKS + ("badge", "lead")),
    "C2": Style("C2", 0xE6E9F2, "\U0001F948", "Elite Runner-Up", "\U0001F948 ELITE \u00B7 PERINGKAT 2",
                "\U0001F948 \u2501\u2501\u2501\u2501\u2501\u2501 \u2726 \u2501\u2501\u2501\u2501\u2501\u2501 \U0001F948",
                "Satu langkah dari takhta.", _BASE_PERKS + ("badge",)),
    "C3": Style("C3", 0xE98A3C, "\U0001F949", "Elite Top 3", "\U0001F949 ELITE \u00B7 PERINGKAT 3",
                "\U0001F949 \u2501\u2501\u2501\u2501\u2501\u2501 \u2726 \u2501\u2501\u2501\u2501\u2501\u2501 \U0001F949",
                "Tiga besar pembeli terbaik NOCTRA.", _BASE_PERKS + ("badge",)),
}


def pick_level(spend: float, rank: int | None, total_spenders: int, tiers: list) -> tuple[int, int | None]:
    """(level 0..5, crown 1/2/3 atau None).
    * belum belanja -> L0
    * top 3 leaderboard -> crown (level 5)
    * ada tier yang diatur -> dari jumlah tier yang udah kecapai (tier
      teratas selalu L5)
    * gak ada tier -> persentil peringkat."""
    if spend <= 0:
        return 0, None
    crown = rank if rank in (1, 2, 3) else None
    if crown:
        return LEVELS, crown
    if tiers:
        reached = sum(1 for t in tiers if t.min_spend <= spend)
        return (max(1, math.ceil(reached * LEVELS / len(tiers))) if reached else 1), None
    if rank and total_spenders:
        p = rank / total_spenders
        for limit, level in ((0.05, 5), (0.15, 4), (0.35, 3), (0.60, 2)):
            if p <= limit:
                return level, None
    return 1, None


def style_for(level: int, crown: int | None = None) -> Style:
    if crown in (1, 2, 3):
        return STYLES[f"C{crown}"]
    return STYLES[f"L{max(0, min(LEVELS, level))}"]


# ============================================================================
# FORMAT
# ============================================================================

def rank_medal(rank: int | None) -> str:
    return {1: "\U0001F947", 2: "\U0001F948", 3: "\U0001F949"}.get(rank or 0, "\U0001F3C5")


def progress_bar(fraction: float, width: int = 10) -> str:
    filled = max(0, min(width, round(fraction * width)))
    return "\u25B0" * filled + "\u25B1" * (width - filled)


def parse_db_time(text: str | None) -> datetime | None:
    """'YYYY-MM-DD HH:MM:SS' (UTC, kayak datetime('now') SQLite) -> datetime."""
    if not text:
        return None
    try:
        return datetime.strptime(str(text)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def discord_ts(moment: datetime, style: str = "D") -> str:
    """Timestamp Discord -- otomatis tampil sesuai zona waktu & bahasa
    masing-masing yang ngeliat (D = tanggal lengkap, d = singkat, R = relatif)."""
    return f"<t:{int(moment.timestamp())}:{style}>"


def money(amount: float, currency: str) -> str:
    return format_price(amount, currency)


def core_lines(d: CardData) -> list[str]:
    """Blok statistik utama (di samping avatar): nama, peringkat, pesanan, spend."""
    lines = [f"**{d.name}**"]
    if d.rank:
        lines.append(f"{rank_medal(d.rank)} Peringkat **#{d.rank}** dari {d.total_spenders} pembeli")
    else:
        lines.append("\U0001F3C5 Peringkat **-** (belum ada pesanan)")
    lines.append(f"\U0001F9FE Total pesanan **{d.orders}**")
    lines.append(f"\U0001F4B0 Total spend **{money(d.spend, d.currency)}**")
    if d.tier_name:
        lines.append(f"\U0001F3F7\uFE0F Tier **{d.tier_name}**")
    if d.gap_to_next is not None and d.rank and d.rank > 1:
        lines.append(f"\U0001F4CD {money(d.gap_to_next, d.currency)} lagi buat naik ke peringkat #{d.rank - 1}")
    return lines


def perk_lines(style: Style, d: CardData) -> list[str]:
    """Info eksklusif yang kebuka di level ini (yang datanya ada)."""
    out: list[str] = []
    for perk in style.perks:
        if perk == "since" and d.first_order_at:
            out.append(f"\U0001F4C5 Member sejak {discord_ts(d.first_order_at, 'D')}")
        elif perk == "favorite" and d.favorite_name:
            out.append(f"\u2B50 Produk favorit **{d.favorite_name}** ({d.favorite_qty}x)")
        elif perk == "average" and d.orders:
            out.append(f"\U0001F4C8 Rata-rata per pesanan {money(d.average, d.currency)}")
        elif perk == "biggest" and d.biggest:
            out.append(f"\U0001F3C6 Pesanan terbesar {money(d.biggest, d.currency)}")
        elif perk == "last" and d.last_order_at:
            out.append(f"\u26A1 Pesanan terakhir {discord_ts(d.last_order_at, 'R')}")
        elif perk == "badge" and d.badge_text:
            out.append(f"\U0001F396\uFE0F Badge **{d.badge_text}**")
        elif perk == "lead" and d.lead_over_second is not None:
            out.append(f"\U0001F525 Unggul **{money(d.lead_over_second, d.currency)}** dari peringkat 2")
    return out


def progress_lines(d: CardData) -> list[str]:
    if not d.next_tier_name or d.progress is None or d.next_remaining is None:
        return []
    pct = round(d.progress * 100)
    return [
        f"**Menuju {d.next_tier_name}**",
        f"{progress_bar(d.progress)} {pct}% \u00B7 kurang {money(d.next_remaining, d.currency)}",
    ]


def history_entry(order, items) -> str:
    """Satu pesanan di riwayat: produk, harga, tanggal. `items` = baris
    order_items (boleh kosong buat order lama yang belum ke-backfill)."""
    if items:
        def label(it) -> str:
            return f"{it['quantity']}x {it['product_name']}" if it["quantity"] > 1 else it["product_name"]

        names = label(items[0])
        if len(items) > 1:
            names += f" +{len(items) - 1} lainnya"
    else:
        names = f"Pesanan #{order['id']}"
    moment = parse_db_time(order["created_at"])
    date = discord_ts(moment, "d") if moment else "-"
    price = money(order["total_price"], order["currency_label"])
    return f"**{names}**\n-# \U0001F4B0 {price} \u00B7 \U0001F4C5 {date} \u00B7 #{order['id']}"
