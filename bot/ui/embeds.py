"""
Embed builders buat NOCTRA.

Semua embed lewat `base_embed()` biar branding (footer, warna default)
tetep konsisten. Fungsi-fungsi di sini nerima row/value biasa (bukan objek
ORM) biar module ini gak nempel ke database layer.
"""

from __future__ import annotations

from datetime import datetime

import discord

from bot.core.emojis import EMOJI_ERROR, EMOJI_INFO, EMOJI_SUCCESS
from bot.core.theme import (
    COLOR_ACCENT,
    COLOR_DANGER,
    COLOR_MUTED,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    FOOTER_TEXT,
    MARK_BULLET,
    MARK_DASH,
    STATUS_COLORS,
    rating_bar,
    star_rating,
)
from bot.utils.helpers import calculate_final_price, discount_label, format_price


def base_embed(
    title: str,
    description: str | None = None,
    color: int = COLOR_PRIMARY,
    thumbnail_url: str | None = None,
    image_url: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text=FOOTER_TEXT)
    embed.timestamp = datetime.utcnow()
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    if image_url:
        embed.set_image(url=image_url)
    return embed


def error_embed(message: str) -> discord.Embed:
    return base_embed(f"{EMOJI_ERROR} Yah, Gagal", message, color=COLOR_DANGER)


def success_embed(message: str) -> discord.Embed:
    return base_embed(f"{EMOJI_SUCCESS} Berhasil", message, color=COLOR_SUCCESS)


def info_embed(title: str, message: str, image_url: str | None = None) -> discord.Embed:
    return base_embed(f"{EMOJI_INFO} {title}", message, color=COLOR_ACCENT, image_url=image_url)


# -- Katalog -----------------------------------------------------------------

def category_list_embed(categories: list) -> discord.Embed:
    embed = base_embed("NOCTRA -- Kategori", color=COLOR_PRIMARY)
    if not categories:
        embed.description = "Belum ada kategori yang dibuat nih."
        return embed
    lines = []
    for cat in categories:
        state = "aktif" if cat["enabled"] else "nonaktif"
        emoji_prefix = f"{cat['emoji']} " if cat["emoji"] else ""
        lines.append(
            f"{MARK_BULLET} {emoji_prefix}**#{cat['id']} -- {cat['name']}** "
            f"{MARK_DASH} posisi {cat['position']} {MARK_DASH} {state}"
        )
        if cat["description"]:
            lines.append(f"    {cat['description']}")
    embed.description = "\n".join(lines)
    return embed


def category_type_list_embed(category_types: list) -> discord.Embed:
    embed = base_embed("NOCTRA -- Tipe Kategori", color=COLOR_PRIMARY)
    if not category_types:
        embed.description = "Belum ada tipe kategori yang dibuat nih."
        return embed
    lines = []
    for ct in category_types:
        state = "aktif" if ct["enabled"] else "nonaktif"
        emoji_prefix = f"{ct['emoji']} " if ct["emoji"] else ""
        lines.append(
            f"{MARK_BULLET} {emoji_prefix}**#{ct['id']} -- {ct['name']}** "
            f"{MARK_DASH} kategori #{ct['category_id']} {MARK_DASH} posisi {ct['position']} {MARK_DASH} {state}"
        )
        if ct["description"]:
            lines.append(f"    {ct['description']}")
    embed.description = "\n".join(lines)
    return embed


def product_summary_line(product, rating_summary: dict | None = None) -> str:
    final = calculate_final_price(
        product["base_price"], product["discount_type"], product["discount_value"]
    )
    price_text = format_price(final, product["currency_label"])
    dlabel = discount_label(product["discount_type"], product["discount_value"])
    if dlabel:
        price_text += f" ({dlabel}, awalnya {format_price(product['base_price'], product['currency_label'])})"
    rating_text = ""
    if rating_summary and rating_summary["total"]:
        rating_text = f" {MARK_DASH} {rating_summary['average']:.1f}/5 ({rating_summary['total']} ulasan)"
    visibility = "" if product["visible"] else " [disembunyikan]"
    emoji_prefix = f"{product['emoji']} " if product["emoji"] else ""
    return f"{MARK_BULLET} {emoji_prefix}**{product['name']}**{visibility} {MARK_DASH} {price_text}{rating_text}"


def product_list_embed(category_type, products: list) -> discord.Embed:
    if category_type:
        emoji_prefix = f"{category_type['emoji']} " if category_type["emoji"] else ""
        title = f"NOCTRA -- {emoji_prefix}{category_type['name']}"
    else:
        title = "NOCTRA -- Produk"
    embed = base_embed(title, color=COLOR_PRIMARY)
    if not products:
        embed.description = "Belum ada produk di tipe kategori ini."
        return embed
    embed.description = "\n".join(product_summary_line(p) for p in products)
    return embed


# product_detail_embed pindah ke bot.ui.components.product_detail_container()
# -- sekarang dirender pake Components V2, bukan Embed biasa.


# -- Order / Ticket -----------------------------------------------------------

def order_summary_embed(
    order_row,
    product_row,
    payment_row,
    field_values: list | None = None,
) -> discord.Embed:
    status = order_row["status"]
    color = STATUS_COLORS.get(status, COLOR_PRIMARY)
    embed = base_embed(f"Order #{order_row['id']}", color=color)

    embed.add_field(name="Produk", value=product_row["name"], inline=True)
    embed.add_field(
        name="Harga",
        value=format_price(order_row["total_price"], order_row["currency_label"]),
        inline=True,
    )
    embed.add_field(name="Status", value=status.title(), inline=True)
    embed.add_field(
        name="Pembayaran", value=order_row["payment_status"].title(), inline=True
    )
    if payment_row:
        embed.add_field(name="Metode Bayar", value=payment_row["name"], inline=True)
    elif order_row["paid_with_credit"]:
        embed.add_field(name="Metode Bayar", value="Kartu NOCTRA (Saldo Credit)", inline=True)
    embed.add_field(
        name="Dipesan",
        value=f"<t:{int(datetime.fromisoformat(order_row['created_at']).timestamp())}:R>",
        inline=True,
    )

    if field_values:
        lines = []
        for fv in field_values:
            value = fv["value"]
            if fv["field_type"] == "password" and value:
                value = "*" * min(len(value), 12)
            lines.append(f"{MARK_BULLET} **{fv['label']}:** {value}")
        embed.add_field(name="Data yang Kamu Kirim", value="\n".join(lines), inline=False)

    return embed


# order_invoice_embed pindah ke bot.ui.components.invoice_view() -- itu
# masih dirender pake Components V2. purchase_announcement_embed di bawah
# ini TETEP embed klasik (sempet dicoba V2, tapi di-revert balik).


def purchase_announcement_embed(
    buyer_display: str,
    buyer_avatar_url: str | None,
    product_row,
    category_type_row,
    order_row,
) -> discord.Embed:
    """Kartu publik "Si X baru aja beli Y" yang diposting ke channel
    purchase-feed begitu order ditandain selesai. Gaya visualnya sama kayak
    review_card_embed -- author tag kecil plus thumbnail gede dari avatar
    yang sama, soalnya icon author yang kecil suka gak keliatan."""
    price_text = format_price(order_row["total_price"], order_row["currency_label"])
    type_label = product_row["product_type"].replace("_", " ").title()

    embed = base_embed(
        f"{EMOJI_SUCCESS} Pembelian Baru",
        f"**{buyer_display}** baru aja beli **{product_row['name']}**!",
        color=COLOR_SUCCESS,
        thumbnail_url=buyer_avatar_url,
        image_url=product_row["image_url"] or None,
    )
    embed.set_author(name=buyer_display, icon_url=buyer_avatar_url or None)

    embed.add_field(name="Produk", value=product_row["name"], inline=True)
    if category_type_row:
        cat_emoji = f"{category_type_row['emoji']} " if category_type_row["emoji"] else ""
        embed.add_field(name="Kategori", value=f"{cat_emoji}{category_type_row['name']}", inline=True)
    embed.add_field(name="Tipe", value=type_label, inline=True)
    embed.add_field(name="Harga", value=f"**{price_text}**", inline=True)

    return embed


# -- Iklan ---------------------------------------------------------------------

def advertisement_embed(
    title: str,
    description: str,
    color: int = COLOR_ACCENT,
) -> discord.Embed:
    """Embed buat command `/iklan`. Sengaja tetep pake Embed klasik (bukan
    Components V2) -- lebih gampang di-review sebelum posting (preview
    langsung di respon ephemeral) dan cukup buat kebutuhan iklan: title,
    deskripsi, banner gede, thumbnail kecil, warna aksen custom.

    Gambar/thumbnail SENGAJA gak diisi di sini -- caller (bot.cogs.advertisement)
    yang nempelin lewat `embed.set_image()`/`embed.set_thumbnail()`, soalnya
    bisa dari upload attachment (`attachment://...`) atau URL langsung, dan
    builder ini gak perlu tau bedanya."""
    return base_embed(title, description, color=color)


# -- Sambutan Member Baru --------------------------------------------------------
# welcome_embed() di bawah ini UDAH GAK DIPAKE lagi -- /welcome sekarang dirender
# pake Components V2, lihat bot.ui.components.welcome_container(). Ditinggalin
# di sini (bukan dihapus) buat jaga-jaga ada kode lain yang masih manggil.

def welcome_embed(
    member: discord.Member,
    title: str,
    description: str,
    footer_text: str,
    footer_icon_url: str | None = None,
    banner_url: str | None = None,
    color: int = COLOR_ACCENT,
) -> discord.Embed:
    """Embed sambutan buat member baru (dipake bot.cogs.welcome, dipicu
    dari event on_member_join). Beda dari base_embed() biasa -- footer di
    sini KUSTOM (teks + icon-nya staff yang atur lewat /welcome setup),
    bukan footer brand baku, soalnya /welcome dirancang biar staff bisa
    branding sendiri.

    thumbnail SELALU avatar member yang baru gabung (gak bisa diganti --
    itu emang intinya "pesan sambutan personal"), dan ada field "Bergabung"
    otomatis yang nunjukin tanggal & jam join persis pake Discord timestamp
    (ikut nyesuain timezone tiap orang yang liat)."""
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_thumbnail(url=member.display_avatar.url)
    if banner_url:
        embed.set_image(url=banner_url)

    joined_at = member.joined_at or discord.utils.utcnow()
    joined_ts = int(joined_at.timestamp())
    embed.add_field(name="Bergabung", value=f"<t:{joined_ts}:F>  ({MARK_DASH} <t:{joined_ts}:R>)", inline=False)

    embed.set_footer(text=footer_text or None, icon_url=footer_icon_url)
    embed.timestamp = datetime.utcnow()
    return embed


def ticket_welcome_embed(order_summary_text: str | None = None) -> discord.Embed:
    description = (
        "Makasih udah buka ticket. Staff kita bakal bantuin kamu sebentar lagi.\n\n"
        "Tetep di channel ini ya, dan kasih info tambahan kalau diminta staff."
    )
    if order_summary_text:
        description = order_summary_text + "\n\n" + description
    return base_embed("NOCTRA -- Support Ticket", description, color=COLOR_ACCENT)


def ticket_closed_embed(close_reason: str | None, closed_by: str) -> discord.Embed:
    description = f"Ticket ini ditutup sama **{closed_by}**."
    if close_reason:
        description += f"\n\n**Alasan:** {close_reason}"
    return base_embed("Ticket Ditutup", description, color=COLOR_MUTED)


# -- Review ---------------------------------------------------------------------
# review_card_embed() di bawah ini UDAH GAK DIPAKE lagi -- kartu review publik
# sekarang dirender pake Components V2, lihat bot.ui.components.review_card_container().
# Ditinggalin di sini (bukan dihapus) buat jaga-jaga ada kode lain yang masih manggil.

def review_card_embed(
    review_row,
    product_row,
    author_display: str,
    author_avatar_url: str | None = None,
    fallback_banner_url: str | None = None,
    verified: bool = True,
) -> discord.Embed:
    # Slot banner di bawah dipakai gantian: foto review dari customer
    # diutamain kalau ada, baru fallback ke gambar default yang diatur staff
    # lewat /settings review_banner_image -- gak pernah ditumpuk dua-duanya
    # biar tetep rapi. Kalau staff belum atur gambar default, banner-nya
    # kosong aja (gak pake avatar bot atau apapun sebagai pengganti).
    banner_url = review_row["image_url"] or fallback_banner_url

    # Selalu ungu brand di sini -- ini kartu showcase publik, bukan
    # indikator status, jadi gak perlu ikutan ganti warna hijau/merah
    # kayak embed admin internal berdasarkan approved/rejected/hidden.
    embed = base_embed(
        product_row["name"],
        color=COLOR_PRIMARY,
        image_url=banner_url,
        # Icon author doang kecil banget (cuma lingkaran mini di samping
        # nama) -- pasang avatar yang sama di thumbnail bikin versi lebih
        # gede & jelas muncul di pojok kanan atas embed, yang kalau enggak
        # bakal kosong aja di situ.
        thumbnail_url=author_avatar_url,
    )
    embed.set_author(name=author_display, icon_url=author_avatar_url or None)
    embed.add_field(
        name="Rating", value=f"{star_rating(review_row['rating'])}  ({review_row['rating']}/5)", inline=False
    )
    if review_row["review_text"]:
        embed.add_field(name="Ulasan", value=review_row["review_text"], inline=False)
    badge = "Pembelian Terverifikasi" if verified else "Belum Terverifikasi"
    embed.add_field(name="Pembelian", value=badge, inline=True)
    embed.add_field(name="Status", value=review_row["status"].title(), inline=True)
    return embed


def rating_distribution_embed(product_row, summary: dict) -> discord.Embed:
    embed = base_embed(f"{product_row['name']} -- Rating", color=COLOR_PRIMARY)
    if not summary["total"]:
        embed.description = "Belum ada ulasan yang di-approve nih."
        return embed
    embed.description = f"**{summary['average']:.1f}/5** dari {summary['total']} ulasan"
    lines = []
    for star in (5, 4, 3, 2, 1):
        count = summary["distribution"].get(star, 0)
        ratio = count / summary["total"] if summary["total"] else 0
        bar_len = round(ratio * 12)
        lines.append(f"{star} {MARK_DASH} {'█' * bar_len}{'░' * (12 - bar_len)} ({count})")
    embed.add_field(name="Distribusi", value="\n".join(lines), inline=False)
    return embed


# -- Settings / List Admin ------------------------------------------------------

def settings_embed(values: dict) -> discord.Embed:
    embed = base_embed("NOCTRA -- Pengaturan", color=COLOR_PRIMARY)
    for key, value in values.items():
        label = key.replace("_", " ").title()
        embed.add_field(name=label, value=str(value) if value is not None else "Belum diatur", inline=True)
    return embed


def store_status_embed(
    state: str,
    emoji_open: str,
    emoji_closed: str,
    note: str | None = None,
    thumbnail_url: str | None = None,
) -> discord.Embed:
    """Embed publik yang nunjukin toko lagi buka/tutup -- dipake bot.cogs.store_status,
    diedit-in-place tiap kali staff toggle (bukan pesan baru tiap kali) biar
    channel-nya gak kebanjiran pesan lama."""
    is_open = state == "open"
    emoji = emoji_open if is_open else emoji_closed
    label = "SEDANG BUKA" if is_open else "SEDANG TUTUP"
    color = COLOR_SUCCESS if is_open else COLOR_DANGER
    description = f"# {emoji} {label}"
    if note:
        description += f"\n\n{note}"
    embed = base_embed("Status Toko", description, color=color, thumbnail_url=thumbnail_url)
    embed.timestamp = datetime.utcnow()
    return embed


def payment_list_embed(payments: list) -> discord.Embed:
    embed = base_embed("NOCTRA -- Metode Pembayaran", color=COLOR_PRIMARY)
    if not payments:
        embed.description = "Belum ada metode pembayaran yang diatur nih."
        return embed
    lines = []
    for p in payments:
        state = "aktif" if p["enabled"] else "nonaktif"
        has_image = "ada gambar" if p["image_url"] else "belum ada gambar"
        emoji_prefix = f"{p['emoji']} " if p["emoji"] else ""
        lines.append(
            f"{MARK_BULLET} {emoji_prefix}**#{p['id']} -- {p['name']}** {MARK_DASH} {state} "
            f"{MARK_DASH} timeout {p['timeout_minutes']}m {MARK_DASH} {has_image}"
        )
    embed.description = "\n".join(lines)
    return embed
