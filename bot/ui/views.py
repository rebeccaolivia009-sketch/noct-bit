"""
Interactive Views buat NOCTRA: browsing toko (Category -> Category Type ->
Product select), wizard pembelian (dynamic fields -> payment select ->
konfirmasi order), tombol kontrol ticket persistent (support umum aja), dan
alur review button-only (tombol rating -> modal teks opsional).

Gak ada konsep "variant" -- tiap produk di bawah category type itu barang
sendiri yang harganya independen penuh. Dynamic checkout fields nempel di
category type dan otomatis dishare sama semua produk di bawahnya.

Wizard pembelian dan alur review berbasis DM: abis klik "Buy Now" pertama
kali di channel guild, semua langkah selanjutnya (modal dynamic field,
payment select, konfirmasi order, instruksi bayar, dan nanti prompt review)
terjadi di DM customer. Ini bikin seluruh toko guild-agnostic by design --
katalog/order/review yang sama tetep jalan di server manapun bot ini
diundang, soalnya gak ada satupun yang customer-facing yang bergantung ke
channel ticket per-guild. Staff kelola order lewat command `/order` atau
channel order-log opsional (`/settings order_log_channel`).

Persistent views/items (survive restart bot):
  - Custom_id statis, didaftarin lewat `add_view` di setup_hook:
    ShopPanelView, TicketControlView, TicketClaimedView, TicketReopenView,
    OpenTicketPanelView, CardPanelView.
  - Custom_id dinamis (id order/rating/request ke-encode di id-nya
    sendiri), didaftarin lewat `add_dynamic_items` di setup_hook:
    OrderActionButton, ReviewStartButton, CardRequestActionButton.
"""

from __future__ import annotations

from datetime import datetime, timezone

import discord

from bot.core.logger import logger
from bot.core.theme import COLOR_ACCENT
from bot.database.queries import (
    categories as categories_q,
    category_types as category_types_q,
    fields as fields_q,
    orders as orders_q,
    payments as payments_q,
    products as products_q,
    reviews as reviews_q,
    tickets as tickets_q,
)
from bot.database.queries import cards as cards_q
from bot.database.queries import giveaways as giveaways_q
from bot.database.queries import invites as invites_q
from bot.database.queries import leaderboard as lb_q
from bot.database.queries import roblox_listings as roblox_q
from bot.database.queries import ticket_types as ticket_types_q
from bot.ui import components, embeds
from bot.ui.modals import MessageModal, ReasonModal, ReviewTextModal, collect_dynamic_fields
from bot.utils import card_actions, order_actions, ticket_actions
from bot.utils.helpers import RuntimeSettings, calculate_final_price, format_price
from bot.utils.permissions import is_staff
from bot.utils.validators import FieldValidationError, parse_hex_color, validate_field_value

MAX_SELECT_OPTIONS = 25


async def build_join_server_view(db) -> discord.ui.View | None:
    """Tombol link "Gabung Server" -- ditampilin abis customer selesai
    kasih review, ngarahin mereka ke server utama toko. Return None kalau
    link invite-nya belum diatur (/settings main_server_invite), biar
    caller bisa skip nampilin view sama sekali. Tombol link gak butuh
    custom_id dan gak persistent -- Discord yang handle klik-nya langsung
    di sisi client buat buka URL, gak ada interaction yang balik ke bot."""
    runtime = RuntimeSettings(db)
    invite_url = await runtime.main_server_invite_url()
    if not invite_url:
        return None
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(label="Gabung Server", style=discord.ButtonStyle.link, url=invite_url))
    return view


# ============================================================================
# BROWSING TOKO (Category -> Category Type -> Product)
# ============================================================================

class CategorySelect(discord.ui.Select):
    def __init__(self, categories: list):
        options = [
            discord.SelectOption(
                label=cat["name"][:100],
                value=str(cat["id"]),
                description=(cat["description"] or "")[:100] or None,
                emoji=cat["emoji"] or None,
            )
            for cat in categories[:MAX_SELECT_OPTIONS]
        ]
        super().__init__(placeholder="Pilih kategori...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        category_id = int(self.values[0])
        category = await categories_q.get_category(db, category_id)
        category_types = await category_types_q.list_category_types(db, category_id=category_id, enabled_only=True)
        embed = embeds.base_embed(
            f"NOCTRA -- {category['emoji'] + ' ' if category['emoji'] else ''}{category['name']}",
            "Pilih tipe di bawah buat liat produknya.",
            color=COLOR_ACCENT,
        )
        if not category_types:
            embed.description = "Belum ada tipe produk di kategori ini."
        view = CategoryTypeBrowseView(category, category_types)
        await interaction.response.edit_message(embed=embed, view=view)


class CategoryBrowseView(discord.ui.View):
    def __init__(self, categories: list):
        super().__init__(timeout=300)
        self.add_item(CategorySelect(categories))


class CategoryTypeSelect(discord.ui.Select):
    def __init__(self, category_types: list):
        options = [
            discord.SelectOption(
                label=ct["name"][:100],
                value=str(ct["id"]),
                description=(ct["description"] or "")[:100] or None,
                emoji=ct["emoji"] or None,
            )
            for ct in category_types[:MAX_SELECT_OPTIONS]
        ]
        super().__init__(placeholder="Pilih tipe...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        category_type_id = int(self.values[0])
        category_type = await category_types_q.get_category_type(db, category_type_id)
        products = await products_q.list_products(db, category_type_id=category_type_id, visible_only=True)
        embed = embeds.product_list_embed(category_type, products)
        view = ProductBrowseView(category_type, products)
        await interaction.response.edit_message(embed=embed, view=view)


class BackToCategoriesButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Kembali", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        categories = await categories_q.list_categories(db, enabled_only=True)
        embed = embeds.base_embed(
            "NOCTRA STORE", "Pilih kategori di bawah buat liat produk yang ada.", color=COLOR_ACCENT
        )
        view = CategoryBrowseView(categories)
        await interaction.response.edit_message(embed=embed, view=view)


class CategoryTypeBrowseView(discord.ui.View):
    def __init__(self, category, category_types: list) -> None:
        super().__init__(timeout=300)
        if category_types:
            self.add_item(CategoryTypeSelect(category_types))
        self.add_item(BackToCategoriesButton())


class ProductSelect(discord.ui.Select):
    def __init__(self, products: list):
        options = [
            discord.SelectOption(
                label=p["name"][:100], value=str(p["id"]), emoji=p["emoji"] or None
            )
            for p in products[:MAX_SELECT_OPTIONS]
        ]
        super().__init__(placeholder="Liat produk...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        product_id = int(self.values[0])
        product = await products_q.get_product(db, product_id)
        fields = await fields_q.list_fields(db, product["category_type_id"])
        rating_summary = await reviews_q.get_rating_summary(db, product_id)
        view = ProductDetailView(product, fields, rating_summary)
        # Discord ngewajibin embed lama di-clear eksplisit pas pesan pindah
        # ke Components V2 -- kalau enggak, edit-nya ditolak dan interaction
        # timeout ("didn't respond in time") tanpa pesan error yang jelas.
        await interaction.response.edit_message(embed=None, view=view)


class BackToCategoryTypesButton(discord.ui.Button):
    def __init__(self, category_id: int) -> None:
        super().__init__(label="Kembali", style=discord.ButtonStyle.secondary)
        self.category_id = category_id

    async def callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        category = await categories_q.get_category(db, self.category_id)
        category_types = await category_types_q.list_category_types(db, category_id=self.category_id, enabled_only=True)
        embed = embeds.base_embed(
            f"NOCTRA -- {category['emoji'] + ' ' if category and category['emoji'] else ''}{category['name'] if category else ''}",
            "Pilih tipe di bawah buat liat produknya.",
            color=COLOR_ACCENT,
        )
        view = CategoryTypeBrowseView(category, category_types)
        await interaction.response.edit_message(embed=embed, view=view)


class ProductBrowseView(discord.ui.View):
    def __init__(self, category_type, products: list) -> None:
        super().__init__(timeout=300)
        if products:
            self.add_item(ProductSelect(products))
        self.add_item(BackToCategoryTypesButton(category_type["category_id"] if category_type else 0))


class BuyButton(discord.ui.Button):
    def __init__(self, product) -> None:
        super().__init__(label="Beli Sekarang", style=discord.ButtonStyle.success)
        self.product = product

    async def callback(self, interaction: discord.Interaction) -> None:
        await start_purchase(interaction, self.product["id"])


class BackFromProductDetailButton(discord.ui.Button):
    """Tombol Kembali khusus buat kartu produk Components V2. Discord GAK
    ngebolehin ngedit pesan yang udah kepake Components V2 balik ke embed
    klasik (batasan permanen dari API-nya, bukan bug) -- jadi daripada
    edit_message() pesan kartu ini, tombol ini kirim pesan ephemeral BARU
    yang isinya browsing embed klasik. Kartu V2 yang lama dibiarin apa
    adanya (customer bisa dismiss sendiri lewat "Dismiss message")."""

    def __init__(self, category_id: int) -> None:
        super().__init__(label="Kembali", style=discord.ButtonStyle.secondary)
        self.category_id = category_id

    async def callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        category = await categories_q.get_category(db, self.category_id)
        category_types = await category_types_q.list_category_types(db, category_id=self.category_id, enabled_only=True)
        embed = embeds.base_embed(
            f"NOCTRA -- {category['emoji'] + ' ' if category and category['emoji'] else ''}{category['name'] if category else ''}",
            "Pilih tipe di bawah buat liat produknya.",
            color=COLOR_ACCENT,
        )
        view = CategoryTypeBrowseView(category, category_types)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class ProductDetailView(discord.ui.LayoutView):
    """Kartu detail produk -- Components V2. Isinya (harga/tipe/stok/rating)
    dibangun sama components.product_detail_container(), tombol Beli
    Sekarang & Kembali ditempel di sini soalnya butuh callback yang nyambung
    ke alur checkout/browsing lain."""

    def __init__(self, product, fields: list, rating_summary: dict) -> None:
        super().__init__(timeout=300)
        container = components.product_detail_container(product, fields, rating_summary)
        container.add_item(
            discord.ui.ActionRow(
                BuyButton(product),
                BackFromProductDetailButton(product["category_type_id"]),
            )
        )
        self.add_item(container)


class ShopPanelView(discord.ui.LayoutView):
    """Panel persistent yang diposting sekali lewat /settings shop_panel --
    Components V2. Customer klik ini daripada jalanin /shop -- browsing
    sepenuhnya lewat tombol.

    custom_id-nya tetep sama ("noctra:shop:browse") jadi ini tetep jalan
    abis bot restart -- yang dicocokin Discord buat routing klik tombol
    cuma custom_id-nya, bukan isi title/description/gambar panel (itu baked
    di message pas awal diposting, gak perlu match persis pas restart).

    Footer-nya (teks credit + jam "Terakhir update") DIBANGUN ULANG tiap
    kali instance baru dibikin -- baik pas /settings shop_panel pertama
    kali diposting, MAUPUN tiap 10 detik lewat bot.cogs.shop_panel_task
    yang edit-in-place pesannya biar jamnya keliatan jalan otomatis tanpa
    staff perlu posting ulang manual."""

    def __init__(
        self,
        title: str = "NOCTRA STORE",
        description: str = "Klik di bawah buat jelajahin katalog dan pesen -- gak perlu command.",
        banner_url: str | None = None,
        thumbnail_url: str | None = None,
        button_label: str = "Jelajahi Toko",
        button_emoji: str | discord.PartialEmoji | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        super().__init__(timeout=None)
        container = components.shop_panel_container(title, description, banner_url)

        button = discord.ui.Button(
            label=button_label[:80], style=discord.ButtonStyle.secondary,
            custom_id="noctra:shop:browse", emoji=button_emoji,
        )
        button.callback = self.browse
        container.add_item(discord.ui.ActionRow(button))
        container.add_item(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))

        ts = updated_at or datetime.now(timezone.utc)
        footer_text = discord.ui.TextDisplay(
            "-# \u00A9 Credit by Noctra Digital Store \u2014 Category panel \u2014 Search your experience\n"
            f"-# Terakhir update: {ts.strftime('%d %b %Y, %H:%M:%S UTC')}"
        )
        footer_block = (
            discord.ui.Section(footer_text, accessory=discord.ui.Thumbnail(media=thumbnail_url))
            if thumbnail_url else footer_text
        )
        container.add_item(footer_block)
        container.add_item(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))

        self.add_item(container)

    async def browse(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        categories = await categories_q.list_categories(db, enabled_only=True)
        embed = embeds.base_embed(
            "NOCTRA STORE", "Pilih kategori di bawah buat liat produk yang ada.", color=COLOR_ACCENT
        )
        if not categories:
            embed.description = "Toko belum ada kategori yang aktif nih. Cek lagi nanti ya."
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        await interaction.response.send_message(
            embed=embed, view=CategoryBrowseView(categories), ephemeral=True
        )


# ============================================================================
# WIZARD PEMBELIAN (berbasis DM)
# ============================================================================

async def start_purchase(interaction: discord.Interaction, product_id: int) -> None:
    db = interaction.client.db  # type: ignore[attr-defined]
    product = await products_q.get_product(db, product_id)
    if not product or not product["visible"]:
        await interaction.response.send_message(
            embed=embeds.error_embed("Produk ini lagi gak tersedia."), ephemeral=True
        )
        return
    if product["stock_type"] == "manual" and product["stock_quantity"] <= 0:
        await interaction.response.send_message(
            embed=embeds.error_embed("Stok produk ini lagi abis."), ephemeral=True
        )
        return

    dm_channel = await interaction.user.create_dm()
    embed = embeds.info_embed(
        "Lanjutin Order Kamu", f"Klik di bawah buat lanjutin pesen **{product['name']}**."
    )
    try:
        await dm_channel.send(embed=embed, view=ContinueOrderView(product))
    except discord.Forbidden:
        await interaction.response.send_message(
            embed=embeds.error_embed(
                "Gak bisa kirim DM ke kamu buat lanjutin checkout. Aktifin dulu "
                '"Allow direct messages from server members" di Privacy Settings '
                "server ini, terus coba lagi ya."
            ),
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        embed=embeds.success_embed("Cek DM kamu buat lanjutin order ya."), ephemeral=True
    )


async def _delete_source_message(interaction: discord.Interaction) -> None:
    """Hapus pesan DM yang nempel di tombol/select ini, begitu tugasnya
    kelar -- ini yang bikin prompt "Lanjutin Order" / "Pilih Metode
    Pembayaran" gak numpuk terus, gak peduli order-nya kelar atau enggak
    (ringkasan order sendiri dibersihin terpisah, pas selesai, lewat
    tracking order_dm_messages)."""
    message = interaction.message
    if message is None:
        return
    try:
        await message.delete()
    except discord.HTTPException:
        pass  # udah ilang, atau entah kenapa gak bisa dihapus -- gapapa diabaikan


class ContinueOrderButton(discord.ui.Button):
    """Ngasih customer sesuatu buat diklik di DM mereka biar Modal bisa
    dibuka buat checkout fields, soalnya Discord cuma ngebolehin buka Modal
    sebagai respon ke interaction komponen, gak bisa dari pesan bot biasa."""

    def __init__(self, product) -> None:
        super().__init__(label="Lanjutin Order", style=discord.ButtonStyle.success)
        self.product = product

    async def callback(self, interaction: discord.Interaction) -> None:
        await proceed_to_fields(interaction, self.product)
        await _delete_source_message(interaction)


class ContinueOrderView(discord.ui.View):
    def __init__(self, product) -> None:
        super().__init__(timeout=600)
        self.add_item(ContinueOrderButton(product))


async def proceed_to_fields(interaction: discord.Interaction, product) -> None:
    db = interaction.client.db  # type: ignore[attr-defined]
    fields = await fields_q.list_fields(db, product["category_type_id"])

    if not fields:
        await proceed_to_payment(interaction, product, [])
        return

    async def on_fields_complete(inter: discord.Interaction, values_by_id: dict) -> None:
        field_rows = {f["id"]: f for f in fields}
        cleaned, errors = [], []
        for field_id, raw_value in values_by_id.items():
            f = field_rows[field_id]
            try:
                value = validate_field_value(
                    raw_value,
                    required=bool(f["required"]),
                    min_length=f["min_length"],
                    max_length=f["max_length"],
                    validation=f["validation"],
                    label=f["label"],
                )
                cleaned.append({"label": f["label"], "field_type": f["field_type"], "value": value})
            except FieldValidationError as exc:
                errors.append(str(exc))

        if errors:
            await inter.response.send_message(
                embed=embeds.error_embed("\n".join(errors)), ephemeral=False
            )
            return
        await proceed_to_payment(inter, product, cleaned)

    await collect_dynamic_fields(interaction, fields, on_fields_complete)


async def proceed_to_payment(interaction: discord.Interaction, product, field_values: list) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=False, thinking=True)

    db = interaction.client.db  # type: ignore[attr-defined]
    methods = list(await payments_q.list_payment_methods(db, enabled_only=True))

    # Opsi "Bayar pakai Kartu" cuma nongol kalau customer PUNYA kartu --
    # dibikin dict biasa (bukan row payment_methods asli), method_id-nya
    # sengaja string "credit" (bukan int) biar gampang dibedain di
    # finalize_order() dari payment_method beneran (yang PK-nya selalu int).
    card = await cards_q.get_card_by_user(db, interaction.user.id)
    if card:
        currency = await RuntimeSettings(db).default_currency()
        methods.append({
            "id": "credit",
            "name": f"Kartu NOCTRA (Saldo: {format_price(card['credit_balance'], currency)})",
            "instructions": None,
            "image_url": None,
            "timeout_minutes": None,
            "emoji": "💳",
        })

    if not methods:
        await interaction.followup.send(
            embed=embeds.error_embed(
                "Belum ada metode pembayaran yang diatur. Hubungin staff ya."
            ),
            ephemeral=False,
        )
        return

    if len(methods) == 1:
        await finalize_order(interaction, product, field_values, methods[0])
        return

    embed = embeds.info_embed("Pilih Metode Pembayaran", "Pilih cara kamu mau bayar.")
    view = PaymentSelectView(product, field_values, methods)
    await interaction.followup.send(embed=embed, view=view, ephemeral=False)


class PaymentSelect(discord.ui.Select):
    def __init__(self, product, field_values: list, methods: list):
        self.product = product
        self.field_values = field_values
        self.method_map = {str(m["id"]): m for m in methods}
        options = [
            discord.SelectOption(label=m["name"][:100], value=str(m["id"]), emoji=m["emoji"] or None)
            for m in methods[:MAX_SELECT_OPTIONS]
        ]
        super().__init__(placeholder="Pilih metode pembayaran...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        method = self.method_map[self.values[0]]
        await finalize_order(interaction, self.product, self.field_values, method)
        await _delete_source_message(interaction)


class PaymentSelectView(discord.ui.View):
    def __init__(self, product, field_values: list, methods: list) -> None:
        super().__init__(timeout=180)
        self.add_item(PaymentSelect(product, field_values, methods))


async def _finalize_credit_order(
    interaction: discord.Interaction, db, product, field_values: list, unit_price: float
) -> None:
    """Cabang finalize_order() KHUSUS bayar pake Kartu NOCTRA -- beda dari
    pembayaran manual yang nunggu bukti transfer + approve staff, order ini
    LANGSUNG lunas (saldo dipotong di tempat), jadi gak ada langkah "kirim
    bukti" sama sekali. Noctoins yang customer punya OTOMATIS kepake abis
    buat motong harga (sesuai batas saldo Noctoin & harga produk), gak ada
    opsi milih sebagian."""
    card = await cards_q.get_card_by_user(db, interaction.user.id)
    if not card:
        # Race kondisi langka -- kartu kehapus/gak ada pas mereka mencet opsi ini.
        await interaction.followup.send(
            embed=embeds.error_embed("Kartu kamu gak ketemu. Coba pilih metode bayar lain."), ephemeral=False
        )
        return

    runtime = RuntimeSettings(db)
    currency = await runtime.default_currency()
    rate = await runtime.card_noctoin_rate()

    noctoins_used = 0
    if rate > 0 and card["noctoins"] > 0:
        max_affordable = int(unit_price // rate)
        noctoins_used = min(card["noctoins"], max_affordable)
    discount = noctoins_used * rate
    final_price = unit_price - discount

    if card["credit_balance"] < final_price:
        await interaction.followup.send(
            embed=embeds.error_embed(
                f"Saldo Credit kamu gak cukup -- butuh {format_price(final_price, currency)}, "
                f"saldo kamu {format_price(card['credit_balance'], currency)}. Pilih metode bayar lain ya."
            ),
            ephemeral=False,
        )
        return

    stock_reserved = False
    if product["stock_type"] == "manual":
        fresh = await products_q.get_product(db, product["id"])
        if fresh["stock_quantity"] <= 0:
            await interaction.followup.send(
                embed=embeds.error_embed("Yah, produk ini baru aja abis stoknya."), ephemeral=False
            )
            return
        await products_q.adjust_stock(db, product["id"], -1)
        stock_reserved = True

    await cards_q.deduct_credit(db, interaction.user.id, final_price)
    if noctoins_used:
        await cards_q.deduct_noctoins(db, interaction.user.id, noctoins_used)

    order_id = await orders_q.create_order(
        db, interaction.user.id, product["id"], None, unit_price, product["currency_label"],
        stock_reserved, None,
        total_price=final_price, paid_with_credit=True, noctoins_used=noctoins_used,
    )
    await orders_q.set_payment_status(db, order_id, "paid")

    for fv in field_values:
        await orders_q.add_field_value(db, order_id, fv["label"], fv["field_type"], fv["value"])

    order_row = await orders_q.get_order(db, order_id)
    saved_fields = await orders_q.get_field_values(db, order_id)
    order_embed = embeds.order_summary_embed(order_row, product, None, saved_fields)

    note = f"Dibayar otomatis pake **Kartu NOCTRA** -- {format_price(final_price, currency)}."
    if noctoins_used:
        note += f" ({noctoins_used} Noctoins kepake, potongan {format_price(discount, currency)}.)"

    sent_message = await interaction.followup.send(
        content="Order kamu udah dibuat dan LUNAS!",
        embeds=[order_embed, embeds.success_embed(note)],
        ephemeral=False,
        wait=True,
    )
    if sent_message is not None:
        await orders_q.add_dm_message(db, order_id, sent_message.channel.id, sent_message.id)

    # Kabarin staff lewat channel order-log -- pola SAMA PERSIS kayak
    # finalize_order() manual (embed lengkap + field checkout + tombol
    # aksi), BUKAN embed simpel doang, biar staff dapet pengalaman yang
    # sama gak peduli cara bayarnya. Bedanya cuma gak ada tombol Mark Paid
    # (order ini emang udah lunas dari awal).
    log_channel_id = await runtime.order_log_channel_id()
    if log_channel_id:
        log_channel = interaction.client.get_channel(log_channel_id)
        if isinstance(log_channel, discord.TextChannel):
            staff_embed = embeds.order_summary_embed(order_row, product, None, saved_fields)
            staff_embed.add_field(
                name="Customer", value=f"<@{interaction.user.id}> ({interaction.user})", inline=False
            )
            staff_view = discord.ui.View(timeout=None)
            for action in ("mark_completed", "cancel", "refund"):
                staff_view.add_item(OrderActionButton(action, order_id))
            staff_view.add_item(ReplyButton(order_id))
            try:
                await log_channel.send(embed=staff_embed, view=staff_view)
            except discord.HTTPException:
                logger.exception("Gagal posting order Kartu #%s ke channel order-log.", order_id)


async def finalize_order(interaction: discord.Interaction, product, field_values: list, payment) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=False, thinking=True)

    db = interaction.client.db  # type: ignore[attr-defined]
    unit_price = calculate_final_price(product["base_price"], product["discount_type"], product["discount_value"])

    if payment["id"] == "credit":
        await _finalize_credit_order(interaction, db, product, field_values, unit_price)
        return

    stock_reserved = False
    if product["stock_type"] == "manual":
        fresh = await products_q.get_product(db, product["id"])
        if fresh["stock_quantity"] <= 0:
            await interaction.followup.send(
                embed=embeds.error_embed("Yah, produk ini baru aja abis stoknya."), ephemeral=False
            )
            return
        await products_q.adjust_stock(db, product["id"], -1)
        stock_reserved = True

    order_id = await orders_q.create_order(
        db,
        interaction.user.id,
        product["id"],
        payment["id"],
        unit_price,
        product["currency_label"],
        stock_reserved,
        payment["timeout_minutes"],
    )

    for fv in field_values:
        await orders_q.add_field_value(db, order_id, fv["label"], fv["field_type"], fv["value"])

    order_row = await orders_q.get_order(db, order_id)
    saved_fields = await orders_q.get_field_values(db, order_id)
    order_embed = embeds.order_summary_embed(order_row, product, payment, saved_fields)

    reply_embeds = [order_embed]
    if payment["instructions"] or payment["image_url"]:
        reply_embeds.append(
            embeds.info_embed(
                f"Pembayaran -- {payment['name']}",
                payment["instructions"] or "Scan QR code di bawah buat bayar.",
                image_url=payment["image_url"],
            )
        )
    reply_embeds.append(
        embeds.info_embed(
            "Udah Bayar?",
            "Kalau udah bayar, kirim bukti bayarnya (screenshot juga oke) "
            "langsung di DM ini -- bakal otomatis diterusin ke staff, "
            "ditandain sama nomor order ini, jadi gak bakal ketuker sama punya orang lain.",
        )
    )

    sent_message = await interaction.followup.send(
        content="Order kamu udah dibuat! Ini detailnya:",
        embeds=reply_embeds,
        ephemeral=False,
        wait=True,
    )
    if sent_message is not None:
        await orders_q.add_dm_message(db, order_id, sent_message.channel.id, sent_message.id)

    # Kabarin staff lewat channel order-log, kalau diatur. Ini jalan di
    # server manapun bot ada, soalnya channel-nya objek tetap bot-wide --
    # gak harus di guild yang sama tempat customer belanja.
    runtime = RuntimeSettings(db)
    log_channel_id = await runtime.order_log_channel_id()
    if log_channel_id:
        log_channel = interaction.client.get_channel(log_channel_id)
        if isinstance(log_channel, discord.TextChannel):
            staff_embed = embeds.order_summary_embed(order_row, product, payment, saved_fields)
            staff_embed.add_field(name="Customer", value=f"<@{interaction.user.id}> ({interaction.user})", inline=False)
            staff_view = discord.ui.View(timeout=None)
            for action in ("mark_paid", "mark_completed", "cancel", "refund"):
                staff_view.add_item(OrderActionButton(action, order_id))
            staff_view.add_item(ReplyButton(order_id))
            try:
                await log_channel.send(embed=staff_embed, view=staff_view)
            except discord.HTTPException:
                logger.exception("Gagal posting order #%s ke channel order-log.", order_id)

# ============================================================================
# KONTROL TICKET (persistent)
# ============================================================================

def _with_claim_field(embed: discord.Embed, claimant_mention: str | None) -> discord.Embed:
    """Return salinan `embed` dengan field "Diambil Oleh" diset (atau
    dihapus, kalau `claimant_mention` None) -- dipake bareng sama callback
    tombol claim/unclaim biar pesan ticket selalu nunjukin siapa yang lagi
    megang."""
    new_embed = embed.copy()
    for index, field in enumerate(new_embed.fields):
        if field.name == "Diambil Oleh":
            new_embed.remove_field(index)
            break
    if claimant_mention:
        new_embed.add_field(name="Diambil Oleh", value=claimant_mention, inline=True)
    return new_embed


def _source_embed(interaction: discord.Interaction) -> discord.Embed:
    """Embed yang lagi nempel di pesan ticket tempat tombol ini berada,
    dengan fallback aman kalau-kalau pesannya somehow gak punya embed."""
    if interaction.message and interaction.message.embeds:
        return interaction.message.embeds[0]
    return embeds.ticket_welcome_embed()


async def _handle_ticket_close(interaction: discord.Interaction) -> None:
    """Dipake bareng sama tombol Close Ticket di TicketControlView dan
    TicketClaimedView -- diambil atau enggak, cara nutupnya sama aja."""
    ticket = await tickets_q.get_ticket_by_channel(interaction.client.db, interaction.channel.id)  # type: ignore[attr-defined]
    if not ticket:
        await interaction.response.send_message(embed=embeds.error_embed("Ini bukan channel ticket."), ephemeral=True)
        return
    if not (await is_staff(interaction) or interaction.user.id == ticket["user_id"]):
        await interaction.response.send_message(
            embed=embeds.error_embed("Cuma staff atau pemilik ticket yang bisa nutup ticket ini."), ephemeral=True
        )
        return

    async def on_reason(inter: discord.Interaction, reason: str) -> None:
        await inter.response.defer(ephemeral=True)
        await ticket_actions.close_ticket(inter.client, inter.channel, str(inter.user), reason or None)
        await inter.followup.send(embed=embeds.success_embed("Ticket udah ditutup."), ephemeral=True)

    await interaction.response.send_modal(ReasonModal("Tutup Ticket", on_reason))


class TicketDeleteConfirmView(discord.ui.View):
    """Konfirmasi sesaat (gak persistent) buat tombol Hapus Channel --
    ngehapus channel itu permanen dan gak bisa di-undo, jadi ini mastiin
    staff emang niat klik sebelum kejadian."""

    def __init__(self, channel_id: int) -> None:
        super().__init__(timeout=20)
        self.channel_id = channel_id

    @discord.ui.button(label="Ya, Hapus Permanen", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = interaction.client.get_channel(self.channel_id)  # type: ignore[attr-defined]
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.delete(reason=f"Ticket dihapus sama {interaction.user}")
            except discord.HTTPException:
                await interaction.response.edit_message(
                    embed=embeds.error_embed("Gagal hapus channel -- cek permission bot ya."), view=None
                )
                return
        else:
            await interaction.response.edit_message(
                embed=embeds.error_embed("Channel udah gak ada."), view=None
            )
            return
        # Channel-nya udah ilang di titik ini -- pesan konfirmasi ini juga
        # ikut kehapus bareng channel-nya (dia nempel DI channel yang
        # dihapus), jadi gak bisa di-edit lagi (bakal 404 Unknown Message
        # kalau dipaksa). Kirim response BARU (bukan edit) ke staff yang
        # klik -- ini gak butuh pesan lamanya masih ada.
        await interaction.response.send_message(embed=embeds.success_embed("Channel udah dihapus."), ephemeral=True)
        self.stop()

    @discord.ui.button(label="Batal", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(embed=embeds.info_embed("Dibatalin", "Channel gak jadi dihapus."), view=None)
        self.stop()


class TicketReopenView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Buka Lagi Ticket", style=discord.ButtonStyle.primary, custom_id="noctra:ticket:reopen")
    async def reopen(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await is_staff(interaction):
            await interaction.response.send_message(
                embed=embeds.error_embed("Cuma staff yang bisa buka lagi ticket."), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        await ticket_actions.reopen_ticket(interaction.client, interaction.channel, str(interaction.user))
        await interaction.followup.send(embed=embeds.success_embed("Ticket udah dibuka lagi."), ephemeral=True)

    @discord.ui.button(label="Hapus Channel", style=discord.ButtonStyle.danger, custom_id="noctra:ticket:delete")
    async def delete_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await is_staff(interaction):
            await interaction.response.send_message(
                embed=embeds.error_embed("Cuma staff yang bisa hapus channel ticket."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=embeds.error_embed(
                "Ini bakal hapus channel ini secara permanen. Transcript-nya udah kesimpen "
                "(kalau log channel diatur), tapi channel-nya sendiri gak bisa balik lagi. Yakin?"
            ),
            view=TicketDeleteConfirmView(interaction.channel.id),
            ephemeral=True,
        )


ticket_actions._ReopenViewRef.set(TicketReopenView())


class TicketControlView(discord.ui.View):
    """Nempel di ticket support umum aja -- aksi khusus order (Mark
    Paid/Completed/Cancel/Refund) sekarang ada di OrderActionButton di
    channel order-log dan/atau command /order, soalnya order gak lagi bikin
    channel ticket per-order (lihat docstring module).

    Ini state *belum diambil*. Begitu staff klik Claim, pesannya ganti ke
    TicketClaimedView -- lihat callback tombol Claim di bawah."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Ambil Ticket", style=discord.ButtonStyle.primary, custom_id="noctra:ticket:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await is_staff(interaction):
            await interaction.response.send_message(
                embed=embeds.error_embed("Cuma staff yang bisa ngambil ticket."), ephemeral=True
            )
            return
        db = interaction.client.db  # type: ignore[attr-defined]
        ticket = await tickets_q.get_ticket_by_channel(db, interaction.channel.id)
        if not ticket:
            await interaction.response.send_message(embed=embeds.error_embed("Ini bukan channel ticket."), ephemeral=True)
            return
        if ticket["claimed_by"]:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Ticket ini udah diambil sama <@{ticket['claimed_by']}>."),
                ephemeral=True,
            )
            return

        await tickets_q.set_ticket_claim(db, interaction.channel.id, interaction.user.id)
        new_embed = _with_claim_field(_source_embed(interaction), interaction.user.mention)
        await interaction.response.edit_message(embed=new_embed, view=TicketClaimedView())
        await interaction.followup.send(
            embed=embeds.success_embed(f"Ticket udah diambil sama {interaction.user.mention}."), ephemeral=True
        )

    @discord.ui.button(label="Tutup Ticket", style=discord.ButtonStyle.secondary, custom_id="noctra:ticket:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _handle_ticket_close(interaction)


class TicketClaimedView(discord.ui.View):
    """State *udah diambil* -- muncul abis staff klik Claim Ticket. Tombol
    Claim ganti jadi Unclaim; tombol Close Ticket kerjanya sama aja."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Lepas Ticket", style=discord.ButtonStyle.secondary, custom_id="noctra:ticket:unclaim")
    async def unclaim(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        ticket = await tickets_q.get_ticket_by_channel(db, interaction.channel.id)
        if not ticket:
            await interaction.response.send_message(embed=embeds.error_embed("Ini bukan channel ticket."), ephemeral=True)
            return

        is_claimant = ticket["claimed_by"] == interaction.user.id
        is_admin = isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator
        if not (is_claimant or is_admin):
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Cuma staff yang ngambil ticket ini (atau admin) yang bisa lepas ticket ini."
                ),
                ephemeral=True,
            )
            return

        await tickets_q.set_ticket_claim(db, interaction.channel.id, None)
        new_embed = _with_claim_field(_source_embed(interaction), None)
        await interaction.response.edit_message(embed=new_embed, view=TicketControlView())
        await interaction.followup.send(embed=embeds.success_embed("Ticket udah dilepas."), ephemeral=True)

    @discord.ui.button(label="Tutup Ticket", style=discord.ButtonStyle.secondary, custom_id="noctra:ticket:close_claimed")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _handle_ticket_close(interaction)


# ============================================================================
# AKSI ORDER (persistent, dinamis -- diposting di channel order-log)
# ============================================================================

async def _disable_message_buttons(message: discord.Message, *, only_custom_id: str | None = None) -> None:
    """Disable tombol di pesan order-log abis staff nanganin -- biar
    keliatan jelas dari sekilas pandang order mana yang udah diproses vs
    yang masih nunggu (dulu tombolnya tetep bisa diklik terus walau order
    udah kelar, bikin bingung). `only_custom_id` (kalau diisi) cuma
    disable SATU tombol spesifik itu doang -- dipake abis Mark Paid,
    soalnya order-nya sendiri masih lanjut diproses (belum final). Kosongin
    buat disable SEMUA tombol -- dipake abis aksi final: Mark Completed,
    Cancel, Refund."""
    view = discord.ui.View.from_message(message, timeout=None)
    for item in view.children:
        if isinstance(item, discord.ui.Button):
            if only_custom_id is None or item.custom_id == only_custom_id:
                item.disabled = True
    try:
        await message.edit(view=view)
    except discord.HTTPException:
        pass


class OrderActionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"noctra:order:(?P<action>mark_paid|mark_completed|cancel|refund):(?P<order_id>[0-9]+)",
):
    """Tombol kontrol staff yang order ID-nya ke-encode langsung di
    custom_id-nya. Beda sama persistent View biasa (satu custom_id tetap
    dipake bareng di semua pesan), ini bikin tiap order dapet tombol Mark
    Paid / Mark Completed / Cancel / Refund sendiri-sendiri yang jalan di
    channel order-log bareng, dan tetep jalan abis bot restart tanpa
    bookkeeping ekstra -- discord.py rekonstruksi tombolnya dari custom_id
    doang."""

    LABELS = {
        "mark_paid": "Tandain Lunas",
        "mark_completed": "Tandain Selesai",
        "cancel": "Batalin",
        "refund": "Refund",
    }
    STYLES = {
        "mark_paid": discord.ButtonStyle.success,
        "mark_completed": discord.ButtonStyle.primary,
        "cancel": discord.ButtonStyle.danger,
        "refund": discord.ButtonStyle.danger,
    }

    def __init__(self, action: str, order_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=self.LABELS[action],
                style=self.STYLES[action],
                custom_id=f"noctra:order:{action}:{order_id}",
            )
        )
        self.action = action
        self.order_id = order_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):  # noqa: D102
        return cls(match["action"], int(match["order_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await is_staff(interaction):
            await interaction.response.send_message(embed=embeds.error_embed("Khusus staff."), ephemeral=True)
            return

        if self.action in ("mark_paid", "mark_completed"):
            await interaction.response.defer(ephemeral=True)
            func = order_actions.mark_paid if self.action == "mark_paid" else order_actions.mark_completed
            ok, message = await func(interaction.client, self.order_id, interaction.user)
            if ok and interaction.message is not None:
                if self.action == "mark_completed":
                    # Aksi final -- semua tombol di-disable, order-nya udah kelar.
                    await _disable_message_buttons(interaction.message)
                else:
                    # Mark Paid doang -- order-nya masih lanjut (belum
                    # completed), jadi cuma tombol ini yang di-disable,
                    # Cancel/Refund/Mark Completed/Reply tetep aktif.
                    own_custom_id = f"noctra:order:{self.action}:{self.order_id}"
                    await _disable_message_buttons(interaction.message, only_custom_id=own_custom_id)
            await interaction.followup.send(
                embed=embeds.success_embed(message) if ok else embeds.error_embed(message), ephemeral=True
            )
            return

        action, order_id = self.action, self.order_id
        order_message = interaction.message

        async def on_reason(inter: discord.Interaction, reason: str) -> None:
            await inter.response.defer(ephemeral=True)
            if action == "cancel":
                ok, message = await order_actions.cancel_order(inter.client, order_id, reason or None, inter.user)
            else:
                ok, message = await order_actions.refund_order(inter.client, order_id, reason or None, inter.user)
            if ok and order_message is not None:
                # Cancel/Refund juga aksi final -- disable semua tombol.
                await _disable_message_buttons(order_message)
            await inter.followup.send(
                embed=embeds.success_embed(message) if ok else embeds.error_embed(message), ephemeral=True
            )

        title = "Batalin Order" if action == "cancel" else "Refund Order"
        await interaction.response.send_modal(ReasonModal(title, on_reason))


class ReplyButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"noctra:order:reply:(?P<order_id>[0-9]+)",
):
    """Ngasih staff cara balesin DM customer sekali klik -- buka modal buat
    ngetik balesan langsung di channel order-log atau di samping pesan
    bukti bayar yang diterusin, gak perlu ngetik /order message tiap kali.
    Trik custom_id restart-safe sama kayak OrderActionButton."""

    def __init__(self, order_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Balas",
                style=discord.ButtonStyle.secondary,
                custom_id=f"noctra:order:reply:{order_id}",
            )
        )
        self.order_id = order_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):  # noqa: D102
        return cls(int(match["order_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await is_staff(interaction):
            await interaction.response.send_message(embed=embeds.error_embed("Khusus staff."), ephemeral=True)
            return

        order_id = self.order_id

        async def on_message(inter: discord.Interaction, text: str) -> None:
            db = inter.client.db  # type: ignore[attr-defined]
            order = await orders_q.get_order(db, order_id)
            if not order:
                await inter.response.send_message(embed=embeds.error_embed("Order gak ketemu."), ephemeral=True)
                return
            embed = embeds.info_embed(f"Pesan soal Order #{order_id}", text)
            sent = await order_actions.send_message_to_customer(
                inter.client, order["user_id"], embed, order_id, actor=inter.user
            )
            await inter.response.send_message(
                embed=embeds.success_embed("Pesan udah dikirim.")
                if sent
                else embeds.error_embed("Gak bisa DM customer -- mungkin DM-nya lagi ditutup."),
                ephemeral=True,
            )

        await interaction.response.send_modal(MessageModal(f"Balas -- Order #{order_id}", on_message))


# ============================================================================
# PANEL SUPPORT TICKET (persistent)
# ============================================================================

class OpenTicketPanelView(discord.ui.View):
    def __init__(self, button_label: str = "Buka Ticket") -> None:
        super().__init__(timeout=None)
        self.open_ticket.label = button_label[:80]

    @discord.ui.button(label="Buka Ticket", style=discord.ButtonStyle.secondary, custom_id="noctra:ticket:open_support")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        channel = await ticket_actions.create_ticket_channel(
            interaction.client, interaction.guild, interaction.user, "support"
        )
        await channel.send(
            content=interaction.user.mention,
            embed=embeds.ticket_welcome_embed(),
            view=TicketControlView(),
        )
        await interaction.followup.send(
            embed=embeds.success_embed(f"Ticket kamu udah dibuat: {channel.mention}"), ephemeral=True
        )


class TicketTypeSelectView(discord.ui.LayoutView):
    """Panel DROPDOWN buat pilih jenis ticket (Customer Service, Konsultasi,
    dst -- diatur staff lewat /ticket type). Components V2 (lihat
    bot.ui.components.ticket_type_panel_container), BUKAN embed biasa
    kayak OpenTicketPanelView yang lama -- tata letaknya sengaja beda:
    judul di atas, deskripsi sejajar thumbnail, dropdown, banner, footer
    (teks + ikon), masing-masing dipisah garis.

    Persistent: custom_id-nya tetap ("noctra:ticket:type_select"), dan
    SELURUH tampilan (judul/deskripsi/banner/footer/opsi dropdown)
    ke-simpen di pesan Discord itu sendiri (server-side), jadi tetep sama
    abis bot restart -- bot cuma perlu tetep bisa nangkep event-nya lewat
    custom_id yang sama pas didaftarin balik lewat add_view() di
    setup_hook(). Kalau daftar ticket_types ATAU tampilan panel berubah,
    staff perlu posting ulang panelnya (`/ticket panel_types`) biar kartu
    yang lama ke-update ngikutin.

    `ticket_types` dikosongin (None/[]) pas didaftarin ulang di
    setup_hook() -- itu instance CUMA buat nangkep interaksi dari pesan
    LAMA yang udah keposting, bukan buat ditampilin lagi, jadi opsi/
    tampilan placeholder di situ gak masalah gak kepake."""

    def __init__(
        self,
        ticket_types: list | None = None,
        *,
        title: str = "NOCTRA -- Pilih Jenis Ticket",
        description: str = "Pilih jenis ticket yang sesuai kebutuhan kamu lewat dropdown di bawah.",
        thumbnail_url: str | None = None,
        banner_url: str | None = None,
        footer_text: str | None = None,
        footer_icon_url: str | None = None,
        color: int = COLOR_ACCENT,
    ) -> None:
        super().__init__(timeout=None)
        options = [
            discord.SelectOption(
                label=t["label"][:100],
                value=t["slug"],
                description=(t["description"] or "")[:100] or None,
                emoji=discord.PartialEmoji.from_str(t["emoji"]) if t["emoji"] else None,
            )
            for t in (ticket_types or [])
        ] or [discord.SelectOption(label="Belum ada jenis ticket diatur", value="_none")]

        select_item = discord.ui.Select(
            placeholder="Pilih jenis ticket...",
            custom_id="noctra:ticket:type_select",
            min_values=1,
            max_values=1,
            options=options,
        )
        select_item.callback = self._make_select_callback(select_item)

        container = components.ticket_type_panel_container(
            title=title,
            description=description,
            select_item=select_item,
            thumbnail_url=thumbnail_url,
            banner_url=banner_url,
            footer_text=footer_text,
            footer_icon_url=footer_icon_url,
            color=color,
        )
        self.add_item(container)

    def _make_select_callback(self, select_item: discord.ui.Select):
        async def _callback(interaction: discord.Interaction) -> None:
            slug = select_item.values[0]
            if slug in ("_none", "_placeholder"):
                await interaction.response.send_message(
                    embed=embeds.error_embed("Belum ada jenis ticket yang diatur staff."), ephemeral=True
                )
                return

            db = interaction.client.db  # type: ignore[attr-defined]
            ticket_type = await ticket_types_q.get_by_slug(db, interaction.guild_id, slug)
            if ticket_type is None or not ticket_type["enabled"]:
                await interaction.response.send_message(
                    embed=embeds.error_embed(
                        "Jenis ticket ini udah gak aktif -- coba pilih yang lain atau hubungi staff."
                    ),
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True, thinking=True)
            channel = await ticket_actions.create_ticket_channel(
                interaction.client, interaction.guild, interaction.user, slug
            )
            await channel.send(
                content=interaction.user.mention,
                embed=embeds.ticket_welcome_embed(),
                view=TicketControlView(),
            )
            await interaction.followup.send(
                embed=embeds.success_embed(f"Ticket **{ticket_type['label']}** kamu udah dibuat: {channel.mention}"),
                ephemeral=True,
            )

        return _callback


# ============================================================================
# ALUR REVIEW (button-only -- gak perlu /review submit)
# ============================================================================

class RatingButton(discord.ui.Button):
    def __init__(self, order_id: int, rating_value: int) -> None:
        super().__init__(label=str(rating_value), style=discord.ButtonStyle.secondary)
        self.order_id = order_id
        self.rating_value = rating_value

    async def callback(self, interaction: discord.Interaction) -> None:
        rating = self.rating_value
        order_id = self.order_id
        anonymous = self.view.anonymous  # type: ignore[union-attr]

        async def on_text(inter: discord.Interaction, text: str) -> None:
            db = inter.client.db  # type: ignore[attr-defined]
            order = await orders_q.get_order(db, order_id)
            if not order or order["user_id"] != inter.user.id:
                await inter.response.send_message(
                    embed=embeds.error_embed("Prompt ini bukan buat kamu."), ephemeral=True
                )
                return
            if order["status"] != "completed" or order["payment_status"] != "paid":
                await inter.response.send_message(
                    embed=embeds.error_embed("Order ini udah gak bisa direview lagi."), ephemeral=True
                )
                return
            if await reviews_q.get_review_by_order(db, order_id):
                await inter.response.send_message(
                    embed=embeds.error_embed("Kamu udah pernah review order ini."), ephemeral=True
                )
                return

            review_id = await reviews_q.create_review(
                db, order_id, order["product_id"], inter.user.id, rating, text or None, anonymous
            )

            # Prompt "Gimana Belanjanya?" yang awal udah kelar tugasnya --
            # bersihin (dan pesan balesan staff yang nyasar) sebelum nanya
            # soal foto, biar itu juga gak numpuk basi.
            await order_actions.cleanup_dm_messages(inter.client, order_id)

            # Discord Modal cuma dukung field teks -- gak ada cara nerima
            # upload file lewat situ. Jadi daripada maksain field URL di
            # modal rating+teks, attachment foto jadi langkah lanjutan
            # sendiri yang pendek: minta customer kirim aja gambarnya kayak
            # pesan DM biasa.
            await reviews_q.set_awaiting_photo(db, review_id, True)
            prompt_embed = embeds.info_embed(
                "Mau Tambahin Foto? (Opsional)",
                "Punya screenshot buat nemenin review kamu? Kirim aja di sini "
                "kayak chat biasa -- gak perlu link. Atau klik Lewati buat "
                "selesai tanpa foto.",
            )
            await inter.response.send_message(
                embed=prompt_embed, view=PhotoPromptView(review_id), ephemeral=False
            )
            sent = await inter.original_response()
            await orders_q.add_dm_message(db, order_id, sent.channel.id, sent.id)

        await interaction.response.send_modal(ReviewTextModal(f"Kasih Rating {rating}/5 -- Tulis Review", on_text))


class SkipPhotoButton(discord.ui.Button):
    def __init__(self, review_id: int) -> None:
        super().__init__(label="Lewati", style=discord.ButtonStyle.secondary)
        self.review_id = review_id

    async def callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        review = await reviews_q.get_review(db, self.review_id)
        if not review or review["user_id"] != interaction.user.id:
            await interaction.response.send_message(
                embed=embeds.error_embed("Prompt ini bukan buat kamu."), ephemeral=True
            )
            return
        await reviews_q.set_awaiting_photo(db, self.review_id, False)
        join_view = await build_join_server_view(db)
        await interaction.response.send_message(
            embed=embeds.success_embed("Santai -- review kamu udah masuk tanpa foto."),
            view=join_view,
            ephemeral=True,
        )
        await order_actions.cleanup_dm_messages(interaction.client, review["order_id"])


class PhotoPromptView(discord.ui.View):
    def __init__(self, review_id: int) -> None:
        super().__init__(timeout=600)
        self.review_id = review_id
        self.add_item(SkipPhotoButton(review_id))


class AnonymousToggleButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Anonim: Nonaktif", style=discord.ButtonStyle.secondary, row=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        view: RatingPromptView = self.view  # type: ignore[assignment]
        view.anonymous = not view.anonymous
        self.label = f"Anonim: {'Aktif' if view.anonymous else 'Nonaktif'}"
        await interaction.response.edit_message(view=view)


class RatingPromptView(discord.ui.View):
    def __init__(self, order_id: int) -> None:
        super().__init__(timeout=300)
        self.order_id = order_id
        self.anonymous = False
        for value in range(1, 6):
            self.add_item(RatingButton(order_id, value))
        self.add_item(AnonymousToggleButton())


class ReviewStartButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"noctra:review:start:(?P<order_id>[0-9]+)",
):
    """Tombol 'Kasih Review' -- di-DM ke customer otomatis begitu staff
    nandain order mereka selesai (lihat bot.utils.order_actions). Order ID
    ke-encode di custom_id-nya jadi ini tetep jalan abis bot restart tanpa
    bookkeeping ekstra, trik yang sama kayak OrderActionButton."""

    def __init__(self, order_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Kasih Review",
                style=discord.ButtonStyle.secondary,
                custom_id=f"noctra:review:start:{order_id}",
            )
        )
        self.order_id = order_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):  # noqa: D102
        return cls(int(match["order_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        order = await orders_q.get_order(db, self.order_id)
        if not order:
            await interaction.response.send_message(embed=embeds.error_embed("Order gak ketemu."), ephemeral=True)
            return
        if order["user_id"] != interaction.user.id:
            await interaction.response.send_message(
                embed=embeds.error_embed("Cuma customer yang mesen ini yang bisa kasih review."),
                ephemeral=True,
            )
            return
        if order["status"] != "completed" or order["payment_status"] != "paid":
            await interaction.response.send_message(
                embed=embeds.error_embed("Order ini belum bisa direview."), ephemeral=True
            )
            return
        if await reviews_q.get_review_by_order(db, self.order_id):
            await interaction.response.send_message(
                embed=embeds.error_embed("Kamu udah pernah review order ini. Pake `/review edit` buat ubah."),
                ephemeral=True,
            )
            return
        embed = embeds.info_embed(
            "Kasih Rating Belanjaan Kamu", "Pilih rating dari 1 sampe 5, terus tulis review (opsional)."
        )
        await interaction.response.send_message(embed=embed, view=RatingPromptView(self.order_id), ephemeral=True)


# ============================================================================
# KARTU DIGITAL (panel persistent + modal nominal + tombol approve staff)
# ============================================================================

async def _create_card_request_and_notify(
    db, dm_channel: discord.DMChannel, user: discord.abc.User, kind: str,
    amount: float, admin_fee: float, payment,
) -> None:
    """Bikin row card_requests + kirim instruksi bayar ke DM -- dipake
    bareng, entah metode bayarnya cuma satu (langsung kepake) atau customer
    pilih lewat CardPaymentSelect. Pola embed-nya niru finalize_order() di
    alur checkout produk biasa (ringkasan + instruksi bayar + "kirim
    bukti")."""
    request_id = await cards_q.create_request(db, user.id, kind, amount, admin_fee)
    currency = await RuntimeSettings(db).default_currency()

    lines = [f"Permintaan kamu (`#{request_id}`) udah dicatet -- **{format_price(amount, currency)}**."]
    if kind == "create":
        lines.append(
            f"Biaya admin pembuatan kartu: {format_price(admin_fee, currency)} "
            f"(Credit yang bakal masuk: {format_price(amount - admin_fee, currency)})."
        )
    reply_embeds = [embeds.info_embed("Permintaan Kartu Dibuat", "\n".join(lines))]
    if payment["instructions"] or payment["image_url"]:
        reply_embeds.append(
            embeds.info_embed(
                f"Pembayaran -- {payment['name']}",
                payment["instructions"] or "Scan QR code di bawah buat bayar.",
                image_url=payment["image_url"],
            )
        )
    reply_embeds.append(
        embeds.info_embed(
            "Udah Bayar?",
            "Kalau udah bayar, kirim bukti bayarnya (screenshot juga oke) langsung di DM ini -- "
            "bakal otomatis diterusin ke staff.",
        )
    )
    await dm_channel.send(embeds=reply_embeds)


class CardPaymentSelect(discord.ui.Select):
    def __init__(self, kind: str, amount: float, admin_fee: float, methods: list) -> None:
        self.kind = kind
        self.amount = amount
        self.admin_fee = admin_fee
        self.method_map = {str(m["id"]): m for m in methods}
        options = [
            discord.SelectOption(label=m["name"][:100], value=str(m["id"]), emoji=m["emoji"] or None)
            for m in methods[:MAX_SELECT_OPTIONS]
        ]
        super().__init__(placeholder="Pilih metode pembayaran...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        payment = self.method_map[self.values[0]]
        await _create_card_request_and_notify(
            db, interaction.channel, interaction.user, self.kind, self.amount, self.admin_fee, payment
        )
        await interaction.response.edit_message(
            embed=embeds.success_embed("Metode pembayaran dipilih -- cek instruksi di atas."), view=None
        )


class CardPaymentSelectView(discord.ui.View):
    def __init__(self, kind: str, amount: float, admin_fee: float, methods: list) -> None:
        super().__init__(timeout=180)
        self.add_item(CardPaymentSelect(kind, amount, admin_fee, methods))


class CardCreateModal(discord.ui.Modal, title="Buat Kartu NOCTRA"):
    def __init__(self, on_submit_callback) -> None:
        super().__init__(timeout=300)
        self._on_submit_callback = on_submit_callback
        self.amount_input = discord.ui.TextInput(
            label="Nominal Deposit",
            style=discord.TextStyle.short,
            placeholder="Contoh: 25000",
            max_length=12,
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit_callback(interaction, self.amount_input.value.strip())


class CardTopupModal(discord.ui.Modal, title="Isi Saldo Kartu"):
    def __init__(self, on_submit_callback) -> None:
        super().__init__(timeout=300)
        self._on_submit_callback = on_submit_callback
        self.amount_input = discord.ui.TextInput(
            label="Nominal Isi Saldo",
            style=discord.TextStyle.short,
            placeholder="Contoh: 50000",
            max_length=12,
        )
        self.add_item(self.amount_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit_callback(interaction, self.amount_input.value.strip())


class CardPanelView(discord.ui.LayoutView):
    """Panel persistent /settings card_panel -- 3 tombol: Buat Kartu, Isi
    Saldo, Cek Saldo. Custom_id-nya statis (bukan dynamic item) soalnya
    tombolnya sama persis buat semua orang, cuma yang beda hasilnya
    berdasarkan siapa yang klik -- sama pola kayak ShopPanelView."""

    def __init__(
        self,
        title: str = "Kartu Digital NOCTRA",
        description: str = "Punya kartu buat belanja lebih gampang -- gak perlu transfer ulang tiap order.",
    ) -> None:
        super().__init__(timeout=None)
        container = components.card_panel_container(title, description)

        create_btn = discord.ui.Button(
            label="Buat Kartu", style=discord.ButtonStyle.success, custom_id="noctra:card:create"
        )
        topup_btn = discord.ui.Button(
            label="Isi Saldo", style=discord.ButtonStyle.primary, custom_id="noctra:card:topup"
        )
        check_btn = discord.ui.Button(
            label="Cek Saldo", style=discord.ButtonStyle.secondary, custom_id="noctra:card:check"
        )
        create_btn.callback = self.on_create
        topup_btn.callback = self.on_topup
        check_btn.callback = self.on_check

        container.add_item(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        container.add_item(discord.ui.ActionRow(create_btn, topup_btn, check_btn))
        self.add_item(container)

    async def on_create(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        if await cards_q.get_card_by_user(db, interaction.user.id):
            await interaction.response.send_message(
                embed=embeds.error_embed("Kamu udah punya kartu -- pake tombol **Isi Saldo** aja."),
                ephemeral=True,
            )
            return
        if await cards_q.get_open_request_for_user(db, interaction.user.id):
            await interaction.response.send_message(
                embed=embeds.error_embed("Kamu masih punya permintaan yang lagi diproses. Tunggu itu kelar dulu ya."),
                ephemeral=True,
            )
            return

        async def on_amount(inter: discord.Interaction, raw_amount: str) -> None:
            await self._handle_amount_submit(inter, raw_amount, kind="create")

        await interaction.response.send_modal(CardCreateModal(on_amount))

    async def on_topup(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        if not await cards_q.get_card_by_user(db, interaction.user.id):
            await interaction.response.send_message(
                embed=embeds.error_embed("Kamu belum punya kartu -- pake tombol **Buat Kartu** dulu."),
                ephemeral=True,
            )
            return
        if await cards_q.get_open_request_for_user(db, interaction.user.id):
            await interaction.response.send_message(
                embed=embeds.error_embed("Kamu masih punya permintaan yang lagi diproses. Tunggu itu kelar dulu ya."),
                ephemeral=True,
            )
            return

        async def on_amount(inter: discord.Interaction, raw_amount: str) -> None:
            await self._handle_amount_submit(inter, raw_amount, kind="topup")

        await interaction.response.send_modal(CardTopupModal(on_amount))

    async def _handle_amount_submit(self, interaction: discord.Interaction, raw_amount: str, *, kind: str) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        # Ngebolehin customer ngetik "25.000" atau "25,000" -- dibersihin
        # jadi digit doang sebelum divalidasi, biar gak ketolak gara-gara
        # format pemisah ribuan yang wajar.
        cleaned = raw_amount.replace(".", "").replace(",", "").strip()
        if not cleaned.isdigit():
            await interaction.response.send_message(
                embed=embeds.error_embed("Nominal harus angka, contoh: 25000."), ephemeral=True
            )
            return
        amount = float(cleaned)
        if amount <= 0:
            await interaction.response.send_message(
                embed=embeds.error_embed("Nominal harus lebih dari 0."), ephemeral=True
            )
            return

        runtime = RuntimeSettings(db)
        currency = await runtime.default_currency()
        admin_fee = await runtime.card_admin_fee() if kind == "create" else 0.0
        if kind == "create" and amount <= admin_fee:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    f"Nominal deposit harus lebih dari biaya admin ({format_price(admin_fee, currency)})."
                ),
                ephemeral=True,
            )
            return

        methods = await payments_q.list_payment_methods(db, enabled_only=True)
        if not methods:
            await interaction.response.send_message(
                embed=embeds.error_embed("Belum ada metode pembayaran yang diatur. Hubungin staff ya."),
                ephemeral=True,
            )
            return

        # Sisanya (pilih metode bayar, instruksi, minta bukti transfer)
        # pindah ke DM -- sama persis pola alur checkout produk biasa
        # (lihat start_purchase), soalnya customer bakal balesin lampiran
        # gambar abis ini, dan itu emang ditangkep listener DM
        # (bot.cogs.card), bukan lewat interaction ephemeral.
        try:
            dm_channel = await interaction.user.create_dm()
        except discord.HTTPException:
            dm_channel = None

        if dm_channel is None:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Gak bisa kirim DM ke kamu. Aktifin dulu "
                    '"Allow direct messages from server members" di Privacy Settings '
                    "server ini, terus coba lagi ya."
                ),
                ephemeral=True,
            )
            return

        if len(methods) == 1:
            await _create_card_request_and_notify(
                db, dm_channel, interaction.user, kind, amount, admin_fee, methods[0]
            )
        else:
            embed = embeds.info_embed("Pilih Metode Pembayaran", "Pilih cara kamu mau bayar.")
            await dm_channel.send(embed=embed, view=CardPaymentSelectView(kind, amount, admin_fee, methods))

        await interaction.response.send_message(
            embed=embeds.success_embed("Cek DM kamu buat lanjutin ya."), ephemeral=True
        )

    async def on_check(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        card = await cards_q.get_card_by_user(db, interaction.user.id)
        if not card:
            await interaction.response.send_message(
                embed=embeds.error_embed("Kamu belum punya kartu -- pake tombol **Buat Kartu** dulu."),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        file = await card_actions.build_card_file(interaction.client, interaction.user.id, card)
        await interaction.followup.send(file=file, ephemeral=True)


class CardRequestActionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"noctra:cardreq:(?P<action>approve|reject):(?P<request_id>[0-9]+)",
):
    """Tombol approve/reject permintaan kartu digital (bikin baru / isi
    saldo) di channel /settings card_requests_channel. Trik custom_id
    restart-safe sama kayak OrderActionButton."""

    LABELS = {"approve": "Approve", "reject": "Reject"}
    STYLES = {"approve": discord.ButtonStyle.success, "reject": discord.ButtonStyle.danger}

    def __init__(self, action: str, request_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label=self.LABELS[action],
                style=self.STYLES[action],
                custom_id=f"noctra:cardreq:{action}:{request_id}",
            )
        )
        self.action = action
        self.request_id = request_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):  # noqa: D102
        return cls(match["action"], int(match["request_id"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await is_staff(interaction):
            await interaction.response.send_message(embed=embeds.error_embed("Khusus staff."), ephemeral=True)
            return

        if self.action == "approve":
            await interaction.response.defer(ephemeral=True)
            ok, message = await card_actions.approve_request(interaction.client, self.request_id, interaction.user)
            await interaction.followup.send(
                embed=embeds.success_embed(message) if ok else embeds.error_embed(message), ephemeral=True
            )
            return

        request_id = self.request_id

        async def on_reason(inter: discord.Interaction, reason: str) -> None:
            await inter.response.defer(ephemeral=True)
            ok, message = await card_actions.reject_request(inter.client, request_id, reason or None, inter.user)
            await inter.followup.send(
                embed=embeds.success_embed(message) if ok else embeds.error_embed(message), ephemeral=True
            )

        await interaction.response.send_modal(ReasonModal("Reject Permintaan Kartu", on_reason))


# ============================================================================
# DISAMBIGUASI BUKTI BAYAR (DM -- dipake kalau customer punya lebih dari
# satu order yang lagi nunggu bayar sekaligus, lihat bot.cogs.payment_proof)
# ============================================================================

class PendingOrderSelect(discord.ui.Select):
    def __init__(self, orders: list, content: str, attachment_urls: list[str]) -> None:
        self.orders_map = {str(o["id"]): o for o in orders}
        self.content = content
        self.attachment_urls = attachment_urls
        options = [
            discord.SelectOption(
                label=f"Order #{o['id']}",
                description=f"{o['total_price']:,.2f} {o['currency_label']}",
                value=str(o["id"]),
            )
            for o in orders[:MAX_SELECT_OPTIONS]
        ]
        super().__init__(placeholder="Pilih ini soal order yang mana...", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        order = self.orders_map[self.values[0]]
        sent = await order_actions.forward_to_staff(
            interaction.client, order["id"], interaction.user, self.content, self.attachment_urls
        )
        if sent:
            await interaction.response.edit_message(
                embed=embeds.success_embed(f"Udah dikirim ke staff buat Order #{order['id']}."), view=None
            )
        else:
            await interaction.response.edit_message(
                embed=embeds.error_embed(
                    "Staff belum atur channel order-log, jadi ini gak bisa diterusin "
                    "otomatis. Tunggu staff cek order kamu manual ya."
                ),
                view=None,
            )


class PendingOrderSelectView(discord.ui.View):
    def __init__(self, orders: list, content: str, attachment_urls: list[str]) -> None:
        super().__init__(timeout=300)
        self.add_item(PendingOrderSelect(orders, content, attachment_urls))


# ============================================================================
# GIVEAWAY -- tombol Join persistent (custom_id dinamis, restart-safe sama
# triknya kayak OrderActionButton/CardRequestActionButton di atas). Gaya
# tombol (label/warna/emoji) direkonstruksi dari tabel `giveaways` tiap kali
# dipanggil balik, soalnya staff bisa custom semua itu pas /giveaway create.
# ============================================================================

GIVEAWAY_BUTTON_STYLES = {
    "primary": discord.ButtonStyle.primary,
    "secondary": discord.ButtonStyle.secondary,
    "success": discord.ButtonStyle.success,
    "danger": discord.ButtonStyle.danger,
}


class GiveawayJoinButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"noctra:giveaway:join:(?P<giveaway_id>[0-9]+)",
):
    def __init__(
        self,
        giveaway_id: int,
        label: str,
        style: discord.ButtonStyle,
        emoji: discord.PartialEmoji | None = None,
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=label[:80],
                style=style,
                emoji=emoji,
                custom_id=f"noctra:giveaway:join:{giveaway_id}",
            )
        )
        self.giveaway_id = giveaway_id

    @classmethod
    def from_giveaway_row(cls, giveaway) -> "GiveawayJoinButton":
        emoji = discord.PartialEmoji.from_str(giveaway["button_emoji"]) if giveaway["button_emoji"] else None
        style = GIVEAWAY_BUTTON_STYLES.get(giveaway["button_style"], discord.ButtonStyle.primary)
        return cls(giveaway["id"], giveaway["button_label"], style, emoji)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):  # noqa: D102
        giveaway_id = int(match["giveaway_id"])
        db = interaction.client.db  # type: ignore[attr-defined]
        giveaway = await giveaways_q.get_giveaway(db, giveaway_id)
        if giveaway is None:
            return cls(giveaway_id, "Ikut Giveaway", discord.ButtonStyle.primary)
        return cls.from_giveaway_row(giveaway)

    async def callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        giveaway = await giveaways_q.get_giveaway(db, self.giveaway_id)
        if giveaway is None or giveaway["status"] != "active":
            await interaction.response.send_message(
                embed=embeds.error_embed("Giveaway ini udah gak aktif lagi."), ephemeral=True
            )
            return

        joined = await giveaways_q.has_entered(db, self.giveaway_id, interaction.user.id)
        if joined:
            await giveaways_q.remove_entry(db, self.giveaway_id, interaction.user.id)
            feedback = embeds.success_embed("Kamu keluar dari giveaway ini.")
        else:
            await giveaways_q.add_entry(db, self.giveaway_id, interaction.user.id)
            feedback = embeds.success_embed("Kamu berhasil ikutan giveaway ini! Semoga beruntung \U0001F340")
        await interaction.response.send_message(embed=feedback, ephemeral=True)

        # Update jumlah peserta di kartu giveaway biar keliatan real-time,
        # tanpa nunggu staff refresh manual.
        count = await giveaways_q.count_entries(db, self.giveaway_id)
        try:
            container = components.giveaway_container(giveaway, count)
            new_button = GiveawayJoinButton.from_giveaway_row(giveaway)
            view = GiveawayView(container, new_button)
            if interaction.message:
                await interaction.message.edit(view=view)
        except discord.HTTPException:
            logger.exception("Gagal update kartu giveaway #%s abis join/leave.", self.giveaway_id)


class GiveawayView(discord.ui.LayoutView):
    """Kartu giveaway aktif -- Components V2, pola sama kayak
    ProductDetailView/ShopPanelView: container dari components.py +
    ActionRow tombol Join ditempel di sini."""

    def __init__(self, container: discord.ui.Container, join_button: GiveawayJoinButton) -> None:
        super().__init__(timeout=None)
        container.add_item(discord.ui.ActionRow(join_button))
        self.add_item(container)


# ============================================================================
# BADGE CUSTOM LEADERBOARD -- CUMA buat top 1-3 Top Spenders. Panel di
# channel khusus (/badge channel), gerbang izin (role dari /badge role +
# beneran lagi top-3 SEKARANG) dicek di dalam callback tombol, bukan pas
# panel diposting -- soalnya siapa yang top-3 berubah terus tiap ada order
# baru. Badge-nya sendiri digambar LANGSUNG ke PNG leaderboard (lihat
# bot.utils.leaderboard_image), bukan komponen Discord.
# ============================================================================

class BadgeSetModal(discord.ui.Modal, title="Atur Badge Leaderboard"):
    """Teks bebas + 2 kode warna hex buat gradient. Gerbang izin (role +
    top-3) udah dicek DI LUAR sebelum modal ini dibuka (lihat
    BadgePanelView._check_eligible), jadi di sini fokus validasi FORMAT
    input doang."""

    def __init__(self) -> None:
        super().__init__(timeout=300)
        self.text_input = discord.ui.TextInput(
            label="Teks Badge",
            style=discord.TextStyle.short,
            max_length=24,
            placeholder="misal: SULTAN NOCTRA",
        )
        self.color_from_input = discord.ui.TextInput(
            label="Warna Awal (hex)",
            style=discord.TextStyle.short,
            max_length=7,
            placeholder="#FF5F6D",
        )
        self.color_to_input = discord.ui.TextInput(
            label="Warna Akhir (hex)",
            style=discord.TextStyle.short,
            max_length=7,
            placeholder="#FFC371",
        )
        self.add_item(self.text_input)
        self.add_item(self.color_from_input)
        self.add_item(self.color_to_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        text = self.text_input.value.strip()
        if not text:
            await interaction.response.send_message(
                embed=embeds.error_embed("Teks badge gak boleh kosong."), ephemeral=True
            )
            return

        color_from, error_from = parse_hex_color(self.color_from_input.value, COLOR_ACCENT)
        if error_from:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Warna awal: {error_from}"), ephemeral=True
            )
            return
        color_to, error_to = parse_hex_color(self.color_to_input.value, COLOR_ACCENT)
        if error_to:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Warna akhir: {error_to}"), ephemeral=True
            )
            return

        db = interaction.client.db  # type: ignore[attr-defined]
        await lb_q.set_badge(
            db, interaction.user.id, text, f"#{color_from:06X}", f"#{color_to:06X}",
        )
        await interaction.response.send_message(
            embed=embeds.success_embed(
                "Badge kamu berhasil disimpen! Leaderboard bakal ke-update sekarang."
            ),
            ephemeral=True,
        )

        # Import ditunda: ngindarin circular import (leaderboard.py bisa
        # aja balik nyentuh module lain yang ujung-ujungnya nyentuh views
        # ini). Fire-and-forget, sama pola kayak order_actions.
        from bot.utils.leaderboard import refresh_leaderboard
        await refresh_leaderboard(interaction.client)


class BadgePanelView(discord.ui.LayoutView):
    """Panel Components V2 di channel /badge channel -- tombol Atur/Hapus
    badge custom leaderboard, pola sama kayak TicketTypeSelectView:
    container dari components.py + ActionRow tombol ditempel di sini.
    Persistent: custom_id tombolnya tetap ("noctra:badge:set" /
    "noctra:badge:clear"), tampilan panel (judul/deskripsi/thumbnail/
    banner/emoji tombol) ke-simpen di pesan Discord itu sendiri
    (server-side), jadi tetep sama abis bot restart -- gak kepengaruh sama
    args default di sini, yang cuma dipake pas /badge panel gak ngisi
    parameter opsionalnya.

    emoji_set/emoji_clear nerima string apa aja yang diterima
    discord.PartialEmoji.from_str: unicode biasa ATAU emoji custom server
    (format <:nama:id> / <a:nama:id>) -- caller (bot.cogs.badge) yang
    validasi format-nya sebelum bikin view ini."""

    def __init__(
        self,
        *,
        title: str = "Atur Badge Leaderboard",
        description: str = (
            "Kamu lagi di TOP 3 Top Spenders? Atur badge custom kamu sendiri di sini -- "
            "teks bebas + 2 warna gradient pilihan kamu, bakal tampil di bawah nama kamu di leaderboard."
        ),
        thumbnail_url: str | None = None,
        banner_url: str | None = None,
        emoji_set: str | discord.PartialEmoji = "\U0001F3F7",
        emoji_clear: str | discord.PartialEmoji = "\U0001F5D1",
    ) -> None:
        super().__init__(timeout=None)
        container = components.badge_panel_container(title, description, thumbnail_url, banner_url)

        set_button = discord.ui.Button(
            label="Atur Badge", style=discord.ButtonStyle.primary,
            custom_id="noctra:badge:set", emoji=emoji_set,
        )
        set_button.callback = self._set_badge_callback
        clear_button = discord.ui.Button(
            label="Hapus Badge", style=discord.ButtonStyle.secondary,
            custom_id="noctra:badge:clear", emoji=emoji_clear,
        )
        clear_button.callback = self._clear_badge_callback

        container.add_item(discord.ui.ActionRow(set_button, clear_button))
        self.add_item(container)

    async def _check_eligible(self, interaction: discord.Interaction) -> bool:
        db = interaction.client.db  # type: ignore[attr-defined]
        runtime = RuntimeSettings(db)

        role_id = await runtime.leaderboard_badge_role_id()
        if not role_id:
            await interaction.response.send_message(
                embed=embeds.error_embed("Fitur badge custom belum diaktifin staff."), ephemeral=True
            )
            return False

        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None or not any(r.id == role_id for r in member.roles):
            await interaction.response.send_message(
                embed=embeds.error_embed("Kamu belum punya role yang dibutuhin buat atur badge ini."),
                ephemeral=True,
            )
            return False

        excluded = await runtime.leaderboard_excluded_user_ids()
        top3 = await lb_q.get_top_spenders(db, limit=3, excluded_user_ids=excluded)
        if not any(row["user_id"] == interaction.user.id for row in top3):
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Badge custom cuma bisa diatur sama yang lagi di TOP 3 Top Spenders sekarang."
                ),
                ephemeral=True,
            )
            return False
        return True

    async def _set_badge_callback(self, interaction: discord.Interaction) -> None:
        if not await self._check_eligible(interaction):
            return
        await interaction.response.send_modal(BadgeSetModal())

    async def _clear_badge_callback(self, interaction: discord.Interaction) -> None:
        if not await self._check_eligible(interaction):
            return
        db = interaction.client.db  # type: ignore[attr-defined]
        deleted = await lb_q.delete_badge(db, interaction.user.id)
        if not deleted:
            await interaction.response.send_message(
                embed=embeds.error_embed("Kamu belum punya badge yang keset."), ephemeral=True
            )
            return
        await interaction.response.send_message(embed=embeds.success_embed("Badge kamu udah dihapus."), ephemeral=True)

        from bot.utils.leaderboard import refresh_leaderboard
        await refresh_leaderboard(interaction.client)


# ============================================================================
# PANEL INVITE TRACKER -- tombol "Generate Link Server" (invite personal
# per user, reuse kalau udah pernah generate sebelumnya) + "Aturan Main"
# (nunjukin teks aturan yang staff atur lewat /invite settings rules,
# ephemeral). Persistent (custom_id tetap): "noctra:invite:generate" /
# "noctra:invite:rules". Listener join/leave & command settingnya ada di
# bot.cogs.invite_tracker.
#
# PENTING soal Aturan Main: teksnya SENGAJA gak dibaked ke instance ini
# (beda dari title/description/banner yang murni visual dan udah
# ke-render permanen di pesan Discord-nya) -- interaction persistent
# selalu di-handle instance GENERIC yang didaftarin bot.py lewat
# add_view(InvitePanelView()) (args default), BUKAN instance spesifik
# yang dibikin /invite panel. Makanya _rules_callback query live ke DB
# tiap diklik, biar perubahan lewat /invite settings rules langsung
# kepake ke SEMUA panel yang udah keposting, gak cuma yang baru.
# ============================================================================

class InvitePanelView(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        title: str = "\U0001F3AF INVITE EVENT -- NOCTRA STORE",
        description: str = (
            "Ajakin temen kamu gabung ke sini -- tiap orang yang join lewat link kamu bakal kehitung "
            "otomatis. Makin banyak yang kamu invite, makin gede peluang kamu menang hadiahnya!"
        ),
        banner_url: str | None = None,
        footer_text: str = "-# NOCTRA STORE  \u2022  Invite Event",
        emoji_generate: str | discord.PartialEmoji = "\U0001F517",
        emoji_rules: str | discord.PartialEmoji = "\U0001F4DC",
    ) -> None:
        super().__init__(timeout=None)
        container = components.invite_panel_container(title, description, banner_url)

        generate_button = discord.ui.Button(
            label="Generate Link Server", style=discord.ButtonStyle.primary,
            custom_id="noctra:invite:generate", emoji=emoji_generate,
        )
        generate_button.callback = self._generate_callback
        rules_button = discord.ui.Button(
            label="Aturan Main", style=discord.ButtonStyle.secondary,
            custom_id="noctra:invite:rules", emoji=emoji_rules,
        )
        rules_button.callback = self._rules_callback

        container.add_item(discord.ui.ActionRow(generate_button, rules_button))
        container.add_item(discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small))
        container.add_item(discord.ui.TextDisplay(footer_text))
        self.add_item(container)

    async def _generate_callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member) or interaction.guild is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Command ini cuma bisa dipake di dalem server."), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        db = interaction.client.db  # type: ignore[attr-defined]
        guild = interaction.guild

        # Reuse link lama kalau user ini udah pernah generate & invite-nya
        # masih valid -- biar gak numpuk invite baru tiap klik ulang.
        existing = await invites_q.get_existing_link_for_owner(db, guild.id, interaction.user.id)
        if existing:
            try:
                current_invites = await guild.invites()
                match = next((inv for inv in current_invites if inv.code == existing["code"]), None)
                if match:
                    total = await invites_q.get_active_count(db, guild.id, interaction.user.id)
                    await interaction.followup.send(
                        embed=embeds.success_embed(
                            f"Link kamu: **https://discord.gg/{match.code}**\n\n"
                            f"Total yang udah join lewat kamu (masih di server): **{total} orang**."
                        ),
                        ephemeral=True,
                    )
                    return
            except discord.Forbidden:
                pass  # lanjut coba bikin baru di bawah

        target_channel = (
            guild.rules_channel or guild.system_channel
            or next(
                (c for c in guild.text_channels if c.permissions_for(guild.me).create_instant_invite),
                None,
            )
        )
        if target_channel is None:
            await interaction.followup.send(
                embed=embeds.error_embed("Bot gak nemu channel yang bisa dipake buat bikin invite di server ini."),
                ephemeral=True,
            )
            return

        try:
            invite = await target_channel.create_invite(
                max_age=0, max_uses=0, unique=True,
                reason=f"Invite tracker -- digenerate {interaction.user} lewat panel.",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                embed=embeds.error_embed(
                    "Bot gak punya izin bikin invite di server ini (butuh permission Create Invite)."
                ),
                ephemeral=True,
            )
            return
        except discord.HTTPException:
            await interaction.followup.send(
                embed=embeds.error_embed("Gagal bikin link invite, coba lagi beberapa saat lagi."), ephemeral=True
            )
            return

        await invites_q.save_invite_link(db, invite.code, guild.id, interaction.user.id)

        await interaction.followup.send(
            embed=embeds.success_embed(
                f"Link kamu udah jadi: **{invite.url}**\n\n"
                "Share ke temen kamu -- tiap yang join lewat link ini otomatis kehitung di leaderboard."
            ),
            ephemeral=True,
        )

    async def _rules_callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        rules_text = await RuntimeSettings(db).invite_rules_text() or "Belum ada aturan main yang diatur staff."
        await interaction.response.send_message(
            embed=embeds.info_embed("Aturan Main -- Invite Event", rules_text), ephemeral=True
        )


# ============================================================================
# PANEL KATALOG ITEM ROBLOX LIMITED -- SATU panel, geser Sebelumnya/
# Selanjutnya buat pindah ANTAR ITEM (beda item, beda gambar, beda
# stock/harga, beda link -- BUKAN banyak foto dari satu item yang sama).
# Tombol navigasi pake dynamic item (RobloxSlideButton, custom_id encode
# catalog_id + arah) pola SAMA PERSIS kayak GiveawayJoinButton -- tetep
# jalan abis bot restart TANPA perlu daftarin RobloxCatalogView lewat
# bot.add_view(), cukup daftarin CLASS RobloxSlideButton-nya doang lewat
# bot.add_dynamic_items() di bot.py. Tombol Link BUKAN dynamic item --
# dia style=link (URL button, di-handle Discord sendiri di client) --
# tapi TETEP ikut di-rebuild tiap geser slide, soalnya URL/labelnya
# beda-beda per item.
#
# State "lagi nampilin item ke berapa" itu SATU per katalog (bukan
# per-viewer) -- disimpen di kolom roblox_catalogs.current_index, jadi
# semua orang yang liat channel itu ngeliat item yang sama; siapa aja
# yang klik Sebelumnya/Selanjutnya geser tampilan buat semua orang.
# ============================================================================

class RobloxSlideButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"noctra:roblox:slide:(?P<catalog_id>[0-9]+):(?P<direction>prev|next)",
):
    def __init__(self, catalog_id: int, direction: str) -> None:
        is_prev = direction == "prev"
        super().__init__(
            discord.ui.Button(
                label="Sebelumnya" if is_prev else "Selanjutnya",
                style=discord.ButtonStyle.secondary,
                emoji="\u25C0" if is_prev else "\u25B6",
                custom_id=f"noctra:roblox:slide:{catalog_id}:{direction}",
            )
        )
        self.catalog_id = catalog_id
        self.direction = direction

    @classmethod
    async def from_custom_id(cls, interaction, item, match):  # noqa: D102
        return cls(int(match["catalog_id"]), match["direction"])

    async def callback(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        catalog = await roblox_q.get_catalog(db, self.catalog_id)
        if catalog is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Katalog ini udah gak ada (mungkin kehapus staff)."), ephemeral=True
            )
            return

        items = await roblox_q.list_items(db, self.catalog_id)
        if not items:
            await interaction.response.send_message(
                embed=embeds.error_embed("Katalog ini belum ada item sama sekali."), ephemeral=True
            )
            return

        current = catalog["current_index"] % len(items)
        step = 1 if self.direction == "next" else -1
        new_index = (current + step) % len(items)  # geser muter -- abis item terakhir balik ke item pertama
        await roblox_q.set_current_index(db, self.catalog_id, new_index)

        view = RobloxCatalogView(catalog, items, new_index)
        await interaction.response.edit_message(view=view)


class RobloxCatalogView(discord.ui.LayoutView):
    """Dibangun ulang tiap kali geser item (lihat RobloxSlideButton.
    callback) DAN tiap /roblox panel diposting -- constructor-nya nerima
    row katalog + list item + index langsung dari DB, biar gampang
    di-rebuild dari data terbaru kapan aja. Tombol Link ikut dibangun
    ulang tiap kali -- URL/labelnya ngikutin item yang lagi keliatan,
    BUKAN statis satu link buat semua item."""

    def __init__(self, catalog, items: list, index: int) -> None:
        super().__init__(timeout=None)
        total = len(items)
        index = index % total if total else 0
        item = items[index] if total else None

        container = components.roblox_catalog_container(catalog["panel_title"], item, index, total)

        prev_button = RobloxSlideButton(catalog["id"], "prev")
        next_button = RobloxSlideButton(catalog["id"], "next")
        if total <= 1:
            # Gak ada gunanya geser-geser kalau item-nya cuma 0/1 --
            # tombolnya tetep ada (biar layout konsisten) tapi dimatiin.
            prev_button.item.disabled = True
            next_button.item.disabled = True

        link_button = discord.ui.Button(
            label=item["link_label"] if item else "Beli Sekarang",
            style=discord.ButtonStyle.link,
            url=item["link_url"] if item else "https://www.roblox.com/",
            disabled=item is None,
        )

        container.add_item(discord.ui.ActionRow(prev_button, link_button, next_button))
        self.add_item(container)
