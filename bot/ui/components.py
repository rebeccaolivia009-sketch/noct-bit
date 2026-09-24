"""
Layout builder buat Discord Components V2 -- pengganti sebagian embed di
embeds.py buat kartu-kartu yang paling sering dilihat customer (invoice,
pengumuman pembelian, detail produk).

Kenapa modul terpisah dari embeds.py: sebuah pesan gak bisa nyampur embed
klasik sama Components V2 -- begitu satu pesan pake LayoutView, dia gak
boleh punya `content`/`embed` sama sekali, semuanya (teks, gambar, tombol)
harus jadi komponen. Jadi builder di sini return `discord.ui.LayoutView`
siap kirim (lewat `view=...`), bukan `discord.Embed`.

Perbedaan visual yang perlu diinget dibanding embed lama:
  * Gak ada "inline field" yang sejajar kayak kolom -- semua ditumpuk jadi
    blok teks markdown (label tebal di atas, value di bawahnya).
  * Gak ada footer/timestamp otomatis -- ditulis manual sebagai baris
    "-# ..." (subtext markdown Discord, teks kecil abu-abu).
  * Warna aksen tampil sebagai garis warna di sisi kiri Container, bukan
    border penuh kayak embed.

Referensi API: discord.py >= 2.6 (Container, Section, TextDisplay,
Thumbnail, MediaGallery, Separator, LayoutView semua ada di discord.ui).
"""

from __future__ import annotations

from datetime import datetime

import discord

from bot.core.emojis import EMOJI_SUCCESS
from bot.core.theme import COLOR_ACCENT, COLOR_PRIMARY, COLOR_SUCCESS, FOOTER_TEXT, MARK_DASH, star_rating
from bot.utils.helpers import calculate_final_price, discount_label, format_price


class NoctraLayout(discord.ui.LayoutView):
    """Layout generik satu-Container -- bungkus Container yang udah jadi
    biar call site bisa langsung `view=components.invoice_view(...)`, mirip
    pola lama `embed=embeds.order_invoice_embed(...)`."""

    def __init__(self, container: discord.ui.Container, *, timeout: float | None = None) -> None:
        super().__init__(timeout=timeout)
        self.add_item(container)


def _footer_line(extra: str | None = None) -> str:
    """Subtext markdown ("-# ...") -- pengganti footer otomatis embed yang
    gak ada di Components V2."""
    text = FOOTER_TEXT if not extra else f"{FOOTER_TEXT}  {MARK_DASH}  {extra}"
    return f"-# {text}"


# -- Panel toko -----------------------------------------------------------------

def shop_panel_container(
    title: str,
    description: str,
    image_url: str | None = None,
    thumbnail_url: str | None = None,
) -> discord.ui.Container:
    """Isi panel /settings shop_panel -- staff isi title/description/gambar
    sendiri lewat parameter command, jadi teks (termasuk bullet list custom
    kayak "» ...") dirender apa adanya, gak diapa-apain sama builder ini.
    Gak ada footer text di sini biar clean -- tombolnya (ditempel sama
    ShopPanelView) udah cukup nutup card-nya."""
    header_text = discord.ui.TextDisplay(f"## {title}\n{description}")
    header = (
        discord.ui.Section(header_text, accessory=discord.ui.Thumbnail(media=thumbnail_url))
        if thumbnail_url
        else header_text
    )

    children: list = [header]
    if image_url:
        children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media=image_url)))

    return discord.ui.Container(*children, accent_colour=COLOR_PRIMARY)


def card_panel_container(title: str, description: str) -> discord.ui.Container:
    """Isi panel /settings card_panel -- tombol Buat Kartu/Isi Saldo/Cek
    Saldo ditempel sama CardPanelView (bot.ui.views), builder ini cuma isi
    teksnya doang, pola sama kayak shop_panel_container."""
    return discord.ui.Container(
        discord.ui.TextDisplay(f"## {title}\n{description}"),
        accent_colour=COLOR_PRIMARY,
    )


# -- Invoice ------------------------------------------------------------------

def invoice_view(
    order_row,
    product_row,
    payment_row,
    bot_avatar_url: str | None = None,
) -> discord.ui.LayoutView:
    """Struk yang dikirim begitu order ditandain selesai. `bot_avatar_url`
    diambil langsung dari foto profil bot -- ganti icon bot, struk ini
    otomatis ikut ganti, gak perlu setting manual apapun."""
    invoice_number = f"NOCTRA-{order_row['id']:06d}"
    completed_ts = int(datetime.utcnow().timestamp())

    header_text = discord.ui.TextDisplay(
        f"## {EMOJI_SUCCESS} Invoice {invoice_number}\nMakasih udah belanja -- ini struk pembelian kamu."
    )
    header = (
        discord.ui.Section(header_text, accessory=discord.ui.Thumbnail(media=bot_avatar_url))
        if bot_avatar_url
        else header_text
    )

    lines = [f"**Barang**\n{product_row['name']}"]
    lines.append(f"**Total Bayar**\n{format_price(order_row['total_price'], order_row['currency_label'])}")
    if payment_row:
        lines.append(f"**Metode Bayar**\n{payment_row['name']}")
    elif order_row["paid_with_credit"]:
        lines.append("**Metode Bayar**\nKartu NOCTRA (Saldo Credit)")
    lines.append(f"**Order ID**\n#{order_row['id']}")
    lines.append(f"**Selesai**\n<t:{completed_ts}:f>")
    detail_block = discord.ui.TextDisplay("\n\n".join(lines))

    container = discord.ui.Container(
        header,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        detail_block,
        discord.ui.Separator(visible=False),
        discord.ui.TextDisplay(_footer_line("Simpan ini buat catatan kamu ya")),
        accent_colour=COLOR_SUCCESS,
    )
    return NoctraLayout(container, timeout=None)


# Pengumuman pembelian ("Pembelian Baru") sempet dicoba di sini pake
# Components V2, tapi di-revert balik ke embed klasik -- lihat
# bot.ui.embeds.purchase_announcement_embed().


# -- Detail produk --------------------------------------------------------------

def product_detail_container(product, fields: list, rating_summary: dict) -> discord.ui.Container:
    """Return Container aja (bukan LayoutView lengkap) -- caller (views.py)
    yang nempelin ActionRow tombol Beli Sekarang / Kembali, soalnya tombol
    itu butuh callback yang nyambung ke alur checkout lain."""
    final = calculate_final_price(product["base_price"], product["discount_type"], product["discount_value"])
    price_text = format_price(final, product["currency_label"])
    dlabel = discount_label(product["discount_type"], product["discount_value"])

    title_line = f"## {product['emoji']} {product['name']}" if product["emoji"] else f"## {product['name']}"
    description = product["description"] or "Belum ada deskripsi."
    header_text = discord.ui.TextDisplay(f"{title_line}\n{description}")
    header = (
        discord.ui.Section(header_text, accessory=discord.ui.Thumbnail(media=product["image_url"]))
        if product["image_url"]
        else header_text
    )

    price_line = f"**{price_text}**"
    if dlabel:
        price_line += f"  {MARK_DASH}  {dlabel} (awalnya {format_price(product['base_price'], product['currency_label'])})"
    type_label = product["product_type"].replace("_", " ").title()
    stock_text = "Unlimited" if product["stock_type"] == "unlimited" else f"Sisa {product['stock_quantity']}"

    info_block = discord.ui.TextDisplay(
        f"**Harga**\n{price_line}\n\n**Tipe**\n{type_label}\n\n**Stok**\n{stock_text}"
    )

    children: list = [
        header,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        info_block,
    ]

    if fields:
        req = [f["label"] for f in fields if f["required"]]
        opt = [f["label"] for f in fields if not f["required"]]
        field_text = ""
        if req:
            field_text += "Wajib diisi: " + ", ".join(req)
        if opt:
            field_text += ("\n" if field_text else "") + "Opsional: " + ", ".join(opt)
        children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        children.append(discord.ui.TextDisplay(f"**Data yang Dibutuhin Pas Checkout**\n{field_text}"))

    children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
    if rating_summary["total"]:
        stars = star_rating(rating_summary["average"])
        rating_text = f"{rating_summary['average']:.1f}/5 {MARK_DASH} {stars} {MARK_DASH} {rating_summary['total']} ulasan"
    else:
        rating_text = "Belum ada ulasan nih."
    children.append(discord.ui.TextDisplay(f"**Rating**\n{rating_text}"))

    children.append(discord.ui.Separator(visible=False))
    children.append(discord.ui.TextDisplay(_footer_line()))

    return discord.ui.Container(*children, accent_colour=COLOR_PRIMARY)


# -- Sambutan Member Baru --------------------------------------------------------

def welcome_container(
    member: discord.Member,
    title: str,
    description: str,
    footer_text: str,
    banner_url: str | None = None,
    color: int = COLOR_ACCENT,
) -> discord.ui.Container:
    """Card sambutan member baru -- gantiin embeds.welcome_embed() lama,
    dipindah ke Components V2 biar tiap bagian (judul, deskripsi, tanggal
    gabung, banner, footer) kepisah jelas pake garis Separator selebar
    card, bukan numpuk jadi satu blok teks kayak embed klasik. Title sendiri
    yang nempel ke thumbnail avatar member (biar avatar tetep sejajar sama
    judulnya), deskripsi turun jadi blok sendiri full-width di bawah garis.

    PENTING soal mention/ping: placeholder {mention} di title/description
    (udah diganti jadi member.mention beneran sama _render_template() di
    welcome.py sebelum nyampe sini) BAKAL NGE-PING beneran begitu kekirim,
    beda sama embed klasik yang gak PERNAH ping apapun formatnya -- soalnya
    TextDisplay di Components V2 diperlakuin kayak message content asli
    buat urusan notifikasi (bukan kayak field embed). Nyala/mati-nya ping
    tetep dikontrol dari LUAR fungsi ini lewat parameter `allowed_mentions`
    pas channel.send() (lihat welcome.py._send_welcome), BUKAN dari sini --
    biar toggle /welcome mention tetep konsisten kepake gimanapun staff
    nulis template judul/deskripsinya sendiri."""
    header_title = discord.ui.TextDisplay(f"## {title}")
    header = discord.ui.Section(header_title, accessory=discord.ui.Thumbnail(media=member.display_avatar.url))

    joined_at = member.joined_at or discord.utils.utcnow()
    joined_ts = int(joined_at.timestamp())
    join_block = discord.ui.TextDisplay(
        f"**Bergabung**\n<t:{joined_ts}:F>  ({MARK_DASH} <t:{joined_ts}:R>)"
    )

    children: list = [
        header,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        discord.ui.TextDisplay(description),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        join_block,
    ]

    if banner_url:
        children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media=banner_url)))

    if footer_text:
        children.append(discord.ui.Separator(visible=False))
        # Icon footer SENGAJA gak ada (dulu ada, dicabut atas request Nikss --
        # bikin baris footer jarak kosong gede/gak rapi soalnya Section
        # selalu ngisi lebar penuh card). Emoji custom bisa langsung ditempel
        # di teks footer-nya sendiri (markdown biasa, sama kayak title/desc)
        # kalau butuh aksen visual, gak perlu icon terpisah.
        children.append(discord.ui.TextDisplay(f"-# {footer_text}"))

    return discord.ui.Container(*children, accent_colour=color)


# -- Notifikasi Server Boost -----------------------------------------------------

def boost_container(
    member: discord.Member,
    title: str,
    description: str,
    footer_text: str,
    total_boosts: int,
    banner_url: str | None = None,
    color: int = COLOR_ACCENT,
) -> discord.ui.Container:
    """Card notifikasi server boost -- thumbnail avatar BOOSTER nempel ke
    blok DESKRIPSI (bukan judul, beda dari welcome_container()) biar judul
    dapet ruang penuh selebar card, gak keliatan mepet/kesempitan pas
    disandingin thumbnail. Total boost server ditampilin sebagai blok
    terpisah (angka doang, tanpa emoji) biar keliatan jelas kayak angka
    "achievement", bukan numpuk di deskripsi. Soal mention/ping: sama kayak
    welcome_container, nyala/matinya ping dikontrol dari LUAR
    (allowed_mentions pas channel.send, lihat boost.py._send_boost), bukan
    dari sini."""
    title_display = discord.ui.TextDisplay(f"## {title}")
    description_section = discord.ui.Section(
        discord.ui.TextDisplay(description),
        accessory=discord.ui.Thumbnail(media=member.display_avatar.url),
    )

    boost_block = discord.ui.TextDisplay(
        f"**Total Boost Server Sekarang**\n{total_boosts:,}"
    )

    children: list = [
        title_display,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        description_section,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        boost_block,
    ]

    if banner_url:
        children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media=banner_url)))

    if footer_text:
        children.append(discord.ui.Separator(visible=False))
        children.append(discord.ui.TextDisplay(f"-# {footer_text}"))

    return discord.ui.Container(*children, accent_colour=color)


# -- Giveaway ---------------------------------------------------------------------

_COLOR_GIVEAWAY_ENDED = 0x5A5A5A  # abu-abu netral -- giveaway berakhir gak pake warna aksen custom staff lagi


def giveaway_container(giveaway_row, entry_count: int) -> discord.ui.Container:
    """Card giveaway publik yang lagi AKTIF -- caller (bot.ui.views) yang
    nempelin ActionRow tombol Join (GiveawayJoinButton), soalnya butuh
    custom_id dinamis + gaya tombol (warna/emoji/label) yang staff atur
    sendiri pas /giveaway create. Kartu ini di-edit-in-place tiap ada yang
    join/leave biar jumlah peserta keliatan real-time."""
    title_text = discord.ui.TextDisplay(f"## \U0001F389 {giveaway_row['title']}")

    ends_ts = int(datetime.fromisoformat(giveaway_row["ends_at"]).timestamp())
    info_lines = [
        f"**Hadiah**\n{giveaway_row['prize']}",
        f"**Jumlah Pemenang**\n{giveaway_row['winner_count']}",
        f"**Berakhir**\n<t:{ends_ts}:F>  ({MARK_DASH} <t:{ends_ts}:R>)",
        f"**Peserta**\n{entry_count} orang",
        f"**Host**\n<@{giveaway_row['host_user_id']}>",
    ]
    if giveaway_row["win_role_id"]:
        info_lines.append(f"**Role Hadiah**\n<@&{giveaway_row['win_role_id']}>")

    children: list = [title_text]
    if giveaway_row["description"]:
        children.append(discord.ui.TextDisplay(giveaway_row["description"]))
    children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
    children.append(discord.ui.TextDisplay("\n\n".join(info_lines)))
    children.append(discord.ui.Separator(visible=False))
    children.append(discord.ui.TextDisplay(_footer_line(f"Giveaway #{giveaway_row['id']}")))

    color = giveaway_row["color"] if giveaway_row["color"] is not None else COLOR_ACCENT
    return discord.ui.Container(*children, accent_colour=color)


def giveaway_ended_container(giveaway_row, winner_ids: list[int]) -> discord.ui.Container:
    """Card giveaway yang UDAH berakhir -- gantiin tombol Join dengan hasil
    pemenang. Dipake bot.utils.giveaway_actions.end_giveaway() buat
    edit-in-place kartu yang lagi aktif jadi ini."""
    title_text = discord.ui.TextDisplay(f"## \U0001F3C6 {giveaway_row['title']} -- Berakhir")

    if winner_ids:
        winners_text = ", ".join(f"<@{uid}>" for uid in winner_ids)
        result_line = f"**Pemenang**\n{winners_text}"
    else:
        result_line = "**Pemenang**\nGak ada peserta yang valid -- giveaway ini gak ada pemenangnya."

    info = discord.ui.TextDisplay(f"**Hadiah**\n{giveaway_row['prize']}\n\n{result_line}")

    children = [
        title_text,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        info,
        discord.ui.Separator(visible=False),
        discord.ui.TextDisplay(_footer_line(f"Giveaway #{giveaway_row['id']}")),
    ]
    return discord.ui.Container(*children, accent_colour=_COLOR_GIVEAWAY_ENDED)


# -- Review publik & bukti foto -------------------------------------------------

def review_card_container(
    review_row,
    product_row,
    author_display: str,
    emoji_title: str,
    emoji_user: str,
    emoji_product: str,
    emoji_rating: str,
    emoji_star_filled: str,
    emoji_star_empty: str,
    emoji_message: str,
    author_avatar_url: str | None = None,
    banner_url: str | None = None,
    verified: bool = True,
) -> discord.ui.Container:
    """Kartu review publik yang diposting ke /settings reviews_channel abis
    staff approve -- ini social proof/reputasi toko, jadi didesain buat
    dibaca cepet: judul tebal + baris User/Product/Rating masing-masing
    pake emoji custom di depannya (bukan field embed klasik). Emoji-nya
    diatur staff lewat /settings review_emoji, sekali ganti kepake di semua
    kartu review berikutnya (bukan per-review)."""
    header_text = discord.ui.TextDisplay(f"## {emoji_title} REVIEW BARU #{review_row['id']}")

    # " ".join (bukan concat langsung) SENGAJA -- jaraknya jangan ngandelin
    # padding bawaan si emoji custom (bisa mepet/nempel kalau paddingnya
    # tipis/gak ada), spasi eksplisit ini mastiin jaraknya konsisten
    # gak peduli emoji apa yang staff atur lewat /settings review_emoji.
    stars = " ".join(
        [emoji_star_filled] * review_row["rating"] + [emoji_star_empty] * (5 - review_row["rating"])
    )
    detail_block = discord.ui.TextDisplay(
        f"{emoji_user} **User** : {author_display}\n"
        f"{emoji_product} **Product** : {product_row['name']}\n"
        f"{emoji_rating} **Rating** : {stars}"
    )
    # Thumbnail avatar nempel ke blok User/Product/Rating, BUKAN ke judul --
    # biar baris judul gak nyisain ruang kosong gede di sebelahnya (atas
    # request Nikss). Beda dari welcome_container yang thumbnail-nya emang
    # sengaja nempel ke judul.
    detail_section = (
        discord.ui.Section(detail_block, accessory=discord.ui.Thumbnail(media=author_avatar_url))
        if author_avatar_url
        else detail_block
    )

    children: list = [
        header_text,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        detail_section,
    ]

    if review_row["review_text"]:
        children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        children.append(discord.ui.TextDisplay(f"{emoji_message} **Pesan**\n> {review_row['review_text']}"))

    # Beda dari author_avatar_url (thumbnail kecil di header): ini foto
    # BESAR full-width, diambil dari foto yang customer kirim sendiri kalau
    # ada, fallback ke banner default staff (/settings review_banner_image)
    # kalau enggak.
    banner = review_row["image_url"] or banner_url
    if banner:
        children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media=banner)))

    children.append(discord.ui.Separator(visible=False))
    badge = "Pembelian Terverifikasi" if verified else "Belum Terverifikasi"
    children.append(discord.ui.TextDisplay(_footer_line(badge)))

    return discord.ui.Container(*children, accent_colour=COLOR_PRIMARY)


def testi_proof_container(
    buyer_display: str,
    product_name: str,
    price_text: str,
    testi_number: int,
    photo_url: str,
    emoji_title: str,
    emoji_buyer: str,
    emoji_product: str,
    emoji_price: str,
    emoji_testi: str,
) -> discord.ui.Container:
    """Notifikasi INTERNAL buat staff begitu foto bukti review masuk lewat
    DM (lihat bot.cogs.review_photo) -- BEDA dari review_card_container di
    atas yang showcase publik nunggu approve dulu; ini langsung kekirim ke
    channel staff (/settings testi_proof_channel) pas fotonya baru aja
    masuk, biar staff bisa langsung cross-check tanpa nunggu approval
    flow. Foto-nya WAJIB ada (caller yang mastiin sebelum manggil ini)."""
    header = discord.ui.TextDisplay(f"## {emoji_title} TESTI MONEY")

    detail_block = discord.ui.TextDisplay(
        f"{emoji_buyer} **Buyer** : {buyer_display}\n"
        f"{emoji_product} **Product** : {product_name}\n"
        f"{emoji_price} **Price** : {price_text}\n"
        f"{emoji_testi} **Testi** : #{testi_number}"
    )
    detail_section = discord.ui.Section(detail_block, accessory=discord.ui.Thumbnail(media=photo_url))

    container = discord.ui.Container(
        header,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        detail_section,
        accent_colour=COLOR_PRIMARY,
    )
    return container


# -- Bukti Pengiriman Barang -------------------------------------------------

def shipment_proof_container(
    customer_display: str,
    category: str,
    product: str,
    shipped_date: str,
    status_label: str,
    photo_url: str,
    emoji_title: str,
    emoji_category: str,
    emoji_product: str,
    emoji_date: str,
    emoji_customer: str,
    emoji_status: str,
    color: int = COLOR_PRIMARY,
) -> discord.ui.Container:
    """Card bukti pengiriman barang -- staff-only, diposting manual lewat
    /shipment kirim. Pola SAMA PERSIS kayak testi_proof_container di atas:
    judul + blok detail (tiap baris punya emoji sendiri, staff yang atur
    lewat /shipment emoji & /shipment emoji_produk -- boleh emoji custom
    dari SERVER LAIN sekalipun, soalnya cuma disimpen apa adanya sebagai
    kode emoji mentah, gak divalidasi harus emoji milik server ini) + foto
    jadi thumbnail di sampingnya. `color` beda-beda sesuai status (lihat
    STATUS_CHOICES di bot.cogs.shipment) -- hijau kalau berhasil dikirim,
    merah kalau dibatalin, dst, biar staff bisa liat sekilas dari warnanya
    doang."""
    header = discord.ui.TextDisplay(f"## {emoji_title} BUKTI PENGIRIMAN BARANG")

    detail_block = discord.ui.TextDisplay(
        f"{emoji_category} **Kategori** : {category}\n"
        f"{emoji_product} **Produk** : {product}\n"
        f"{emoji_date} **Tanggal Pengiriman** : {shipped_date}\n"
        f"{emoji_customer} **Customer** : {customer_display}\n"
        f"{emoji_status} **Status Pengiriman** : {status_label}"
    )
    detail_section = discord.ui.Section(detail_block, accessory=discord.ui.Thumbnail(media=photo_url))

    return discord.ui.Container(
        header,
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
        detail_section,
        accent_colour=color,
    )


# -- Panel Dropdown Jenis Ticket ----------------------------------------------

def ticket_type_panel_container(
    *,
    title: str,
    description: str,
    select_item: discord.ui.Select,
    thumbnail_url: str | None = None,
    banner_url: str | None = None,
    footer_text: str | None = None,
    footer_icon_url: str | None = None,
    color: int = COLOR_PRIMARY,
) -> discord.ui.Container:
    """Card panel buka ticket -- Components V2, SENGAJA beda tata letak
    dari /ticket panel yang lama (embed biasa) biar keliatan beda: judul
    di atas -> pemisah -> deskripsi (sejajar thumbnail kalau diisi) ->
    pemisah -> dropdown jenis ticket -> pemisah -> banner (kalau diisi) ->
    pemisah -> footer (teks + ikon kecil, kalau diisi). Blok-blok yang
    opsional (thumbnail/banner/footer) DIHILANGIN kalau gak diisi staff --
    bukan ditampilin kosong -- biar gak ada spasi/pemisah nganggur.

    `select_item` (dropdown-nya) ditempel LANGSUNG di sini lewat
    ActionRow supaya nempel di DALAM container (bukan ngambang keluar
    kartu), sama pola kayak GiveawayView nempelin tombol Join di
    bot.ui.views."""
    children: list = [
        discord.ui.TextDisplay(f"## {title}"),
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
    ]

    if thumbnail_url:
        children.append(
            discord.ui.Section(
                discord.ui.TextDisplay(description),
                accessory=discord.ui.Thumbnail(media=thumbnail_url),
            )
        )
    else:
        children.append(discord.ui.TextDisplay(description))
    children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))

    children.append(discord.ui.ActionRow(select_item))
    children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))

    if banner_url:
        children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media=banner_url)))
        children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))

    if footer_text or footer_icon_url:
        footer_display = discord.ui.TextDisplay(f"-# {footer_text}" if footer_text else "-# \u200b")
        if footer_icon_url:
            children.append(
                discord.ui.Section(footer_display, accessory=discord.ui.Thumbnail(media=footer_icon_url))
            )
        else:
            children.append(footer_display)

    return discord.ui.Container(*children, accent_colour=color)


# -- Panel Atur Badge Leaderboard ---------------------------------------------

def badge_panel_container(
    title: str,
    description: str,
    thumbnail_url: str | None = None,
    banner_url: str | None = None,
) -> discord.ui.Container:
    """Isi panel /badge panel -- sekarang bisa dikasih thumbnail (nempel di
    samping judul, kayak shop_panel_container) dan banner (gambar
    full-width di bawah teks, kayak ticket_type_panel_container) opsional.
    Tombol Atur/Hapus Badge tetep ditempel caller (bot.ui.views.BadgePanelView)."""
    header_text = discord.ui.TextDisplay(f"## {title}\n{description}")
    header = (
        discord.ui.Section(header_text, accessory=discord.ui.Thumbnail(media=thumbnail_url))
        if thumbnail_url
        else header_text
    )

    children: list = [header]
    if banner_url:
        children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media=banner_url)))

    return discord.ui.Container(*children, accent_colour=COLOR_PRIMARY)


def invite_panel_container(
    title: str,
    description: str,
    banner_url: str | None = None,
) -> discord.ui.Container:
    """Isi panel /invite panel -- tata letak SENGAJA linear, beda dari
    badge_panel_container (thumbnail nempel sejajar judul): banner (kalau
    ada) full-width paling atas, lalu pemisah -> judul -> pemisah ->
    deskripsi -> pemisah. Caller (bot.ui.views.InvitePanelView) nempelin
    ActionRow tombol (Generate Link Server + Aturan Main) langsung
    SETELAH container ini di-return, disusul pemisah lagi + footer
    credit, biar urutan akhirnya persis: banner -> pemisah -> judul ->
    pemisah -> deskripsi -> pemisah -> tombol -> pemisah -> footer."""
    children: list = []
    if banner_url:
        children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media=banner_url)))
        children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))

    children.append(discord.ui.TextDisplay(f"## {title}"))
    children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
    children.append(discord.ui.TextDisplay(description))
    children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))

    return discord.ui.Container(*children, accent_colour=COLOR_PRIMARY)


def roblox_listing_container(
    title: str, image_url: str | None, stock_info: str, index: int, total: int
) -> discord.ui.Container:
    """Isi panel listing item Roblox -- tata letak PERSIS 3 bagian yang
    diminta: judul -> pemisah -> gambar utama -> pemisah -> info stock.
    Indikator posisi slideshow ("Gambar X dari Y") ditumpuk sebagai baris
    kecil DI BAWAH judul (masih satu TextDisplay yang sama), bukan
    komponen terpisah, biar strukturnya tetep 3 bagian sesuai spek.
    Caller (bot.ui.views.RobloxListingView) nempelin ActionRow tombol
    (Sebelumnya / Link / Selanjutnya) langsung SETELAH container ini
    di-return."""
    header = f"## {title}"
    if total > 1:
        header += f"\n-# Gambar {index + 1} dari {total}"

    children: list = [discord.ui.TextDisplay(header)]
    children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))

    if image_url:
        children.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media=image_url)))
    else:
        children.append(discord.ui.TextDisplay("*(belum ada gambar buat item ini)*"))
    children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))

    children.append(discord.ui.TextDisplay(stock_info))
    children.append(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))

    return discord.ui.Container(*children, accent_colour=COLOR_PRIMARY)
