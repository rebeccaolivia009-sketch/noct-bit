"""
Penawaran "Produk Lainnya" (mode "staff tentukan harga").

Alurnya:
  1. Customer isi form -> submit_quote(): permintaan disimpen (custom_quotes)
     dan diposting ke channel order-log dengan tombol [Set Harga] [Tolak].
  2. Staff klik Set Harga -> modal harga (+ catatan opsional) -> customer
     dapat DM penawaran dengan tombol [Lanjut Bayar] [Batalkan].
  3. Customer klik Lanjut Bayar -> alur checkout BIASA (pilih pembayaran ->
     order dibuat -> staff proses). Order baru JADI di titik ini, lewat
     finalize_order yang sama dengan produk katalog (lihat bot.ui.views).
  4. complete_quote(): penawaran ditandai 'ordered', pesan staff di-update,
     DM penawaran dibersihin.

Semua tombol DynamicItem (id penawaran di custom_id), jadi tetap hidup
setelah bot restart. Tombol staff dicek is_staff; tombol customer dicek
pemiliknya.
"""

from __future__ import annotations

import discord

from bot.core.logger import logger
from bot.core.theme import COLOR_ACCENT, COLOR_DANGER, COLOR_PRIMARY, COLOR_SUCCESS, COLOR_WARNING
from bot.database.queries import custom_quotes as quotes_q
from bot.database.queries import products as products_q
from bot.ui import embeds
from bot.ui.modals import ReasonModal
from bot.utils import checkout as checkout_util
from bot.utils import custom_order
from bot.utils.builder_spec import money_text
from bot.utils.helpers import RuntimeSettings, format_price
from bot.utils.permissions import is_staff

_STYLE = discord.ButtonStyle
MAX_FIELDS_SHOWN = 20


async def _say(interaction: discord.Interaction, text: str, *, ok: bool = False) -> None:
    try:
        embed = embeds.success_embed(text) if ok else embeds.error_embed(text)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass


# ============================================================================
# TAMPILAN
# ============================================================================

STATUS_COLOR = {
    "pending": COLOR_WARNING, "priced": COLOR_ACCENT, "ordering": COLOR_ACCENT,
    "ordered": COLOR_SUCCESS, "rejected": COLOR_DANGER, "cancelled": COLOR_DANGER,
}


def status_text(quote) -> str:
    status = quote["status"]
    currency = quote["currency_label"] or ""
    if status == "pending":
        return "\u23F3 Menunggu harga dari staff"
    if status in ("priced", "ordering"):
        text = f"\U0001F4B0 Harga **{format_price(quote['price'], currency)}**"
        text += " \u2014 customer lagi milih pembayaran" if status == "ordering" else " \u2014 menunggu customer"
        if quote["note"]:
            text += f"\n> {quote['note']}"
        return text
    if status == "ordered":
        return f"\u2705 Jadi order #{quote['order_id']}"
    if status == "rejected":
        return "\u2716\uFE0F Ditolak staff" + (f"\n> {quote['note']}" if quote["note"] else "")
    return "\U0001F6AB Dibatalkan customer"


def _field_lines(quote) -> list[tuple[str, str]]:
    out = []
    for f in quotes_q.fields_of(quote)[:MAX_FIELDS_SHOWN]:
        value = str(f.get("value") or "").strip()
        if f.get("field_type") == "password" and value:
            value = "*" * min(len(value), 12)
        out.append((str(f.get("label") or "-")[:256], (value or "-")[:1024]))
    return out


async def _product_name(db, quote) -> str:
    product = await products_q.get_product(db, quote["product_id"]) if quote["product_id"] else None
    return product["name"] if product else custom_order.DEFAULT_LABEL


async def staff_embed(bot, quote) -> discord.Embed:
    name = await _product_name(bot.db, quote)
    embed = embeds.base_embed(f"Permintaan {name} #{quote['id']}", color=STATUS_COLOR.get(quote["status"], COLOR_PRIMARY))
    user = bot.get_user(quote["user_id"])
    embed.add_field(
        name="Customer", value=f"<@{quote['user_id']}>" + (f" ({user})" if user else ""), inline=False
    )
    for label, value in _field_lines(quote):
        embed.add_field(name=label, value=value, inline=False)
    embed.add_field(name="Status", value=status_text(quote), inline=False)
    return embed


def staff_view(quote) -> discord.ui.View | None:
    """Tombol staff -- cuma selama permintaannya masih terbuka."""
    if quote["status"] not in ("pending", "priced", "ordering"):
        return None
    view = discord.ui.View(timeout=None)
    view.add_item(QuotePriceButton(quote["id"], repricing=quote["status"] != "pending"))
    view.add_item(QuoteRejectButton(quote["id"]))
    return view


async def refresh_staff_message(bot, quote_id: int) -> None:
    quote = await quotes_q.get(bot.db, quote_id)
    if quote is None or not quote["staff_channel_id"] or not quote["staff_message_id"]:
        return
    channel = bot.get_channel(quote["staff_channel_id"])
    if channel is None:
        return
    try:
        message = await channel.fetch_message(quote["staff_message_id"])
        await message.edit(embed=await staff_embed(bot, quote), view=staff_view(quote))
    except discord.HTTPException:
        logger.warning("Gagal update pesan staff buat penawaran #%s.", quote_id)


def _customer_embed(quote, name: str, title: str, intro: str) -> discord.Embed:
    embed = embeds.base_embed(f"{title} \u2014 {name}", intro, color=STATUS_COLOR.get(quote["status"], COLOR_PRIMARY))
    if quote["price"] is not None:
        embed.add_field(name="Harga", value=format_price(quote["price"], quote["currency_label"] or ""), inline=True)
    if quote["note"] and quote["status"] != "rejected":
        embed.add_field(name="Catatan dari Staff", value=quote["note"][:1024], inline=False)
    lines = [f"\u2022 **{label}:** {value}" for label, value in _field_lines(quote)]
    if lines:
        embed.add_field(name="Pesananmu", value="\n".join(lines)[:1024], inline=False)
    return embed


# ============================================================================
# KIRIM PERMINTAAN (customer isi form)
# ============================================================================

async def can_accept(bot) -> tuple[bool, str | None]:
    """Permintaan dikirim ke channel order-log -- harus udah diatur."""
    channel_id = await RuntimeSettings(bot.db).order_log_channel_id()
    channel = bot.get_channel(channel_id) if channel_id else None
    if not isinstance(channel, discord.TextChannel):
        return False, "Staff belum bisa nerima permintaan ini (channel order-log belum diatur). Hubungi staff ya."
    return True, None


async def submit_quote(interaction: discord.Interaction, request: checkout_util.CheckoutRequest, cleaned: list) -> None:
    """Form lengkap -> simpen permintaan + kirim ke staff."""
    from bot.ui.views import ContinueOrderView

    bot = interaction.client
    db = bot.db  # type: ignore[attr-defined]

    async def fail(text: str) -> None:
        await interaction.response.send_message(
            embed=embeds.error_embed(text), view=ContinueOrderView(request), ephemeral=False
        )

    cfg = await custom_order.active(db)
    accepted, error = await can_accept(bot)
    if cfg is None or not accepted:
        await fail(error or "Fitur ini lagi gak tersedia. Coba lagi nanti ya.")
        return
    product = await products_q.get_product(db, cfg.product_id)
    quote_id = await quotes_q.create(db, interaction.user.id, cfg.product_id, cleaned, product["currency_label"])
    quote = await quotes_q.get(db, quote_id)

    channel_id = await RuntimeSettings(db).order_log_channel_id()
    channel = bot.get_channel(channel_id)
    try:
        message = await channel.send(
            embed=await staff_embed(bot, quote), view=staff_view(quote),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException:
        logger.exception("Gagal ngirim permintaan #%s ke channel staff.", quote_id)
        await db.execute("DELETE FROM custom_quotes WHERE id = ?", (quote_id,))
        await fail("Permintaanmu gagal diteruskan ke staff. Coba lagi ya.")
        return
    await quotes_q.set_staff_message(db, quote_id, channel.id, message.id)

    view = discord.ui.View(timeout=None)
    view.add_item(QuoteCancelButton(quote_id))
    await interaction.response.send_message(
        embed=embeds.success_embed(
            f"Permintaan **#{quote_id}** udah dikirim ke staff. Mereka bakal nentuin harganya, "
            "terus kamu dapat DM di sini buat lanjut ke pembayaran."
        ),
        view=view,
        ephemeral=False,
    )
    try:
        sent = await interaction.original_response()
        await quotes_q.set_dm_message(db, quote_id, sent.channel.id, sent.id)
    except discord.HTTPException:
        pass


# ============================================================================
# DM CUSTOMER
# ============================================================================

async def _delete_dm_message(bot, quote) -> None:
    if not quote["dm_channel_id"] or not quote["dm_message_id"]:
        return
    try:
        channel = bot.get_channel(quote["dm_channel_id"]) or await bot.fetch_channel(quote["dm_channel_id"])
        await channel.get_partial_message(quote["dm_message_id"]).delete()
    except discord.HTTPException:
        pass


async def _dm_user(bot, user_id: int, **kwargs) -> discord.Message | None:
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        return await user.send(**kwargs)
    except discord.HTTPException:
        return None


async def send_price_dm(bot, quote_id: int) -> bool:
    """Kirim penawaran harga ke customer (gantiin DM sebelumnya). False kalau DM-nya ditutup."""
    quote = await quotes_q.get(bot.db, quote_id)
    name = await _product_name(bot.db, quote)
    view = discord.ui.View(timeout=None)
    view.add_item(QuotePayButton(quote_id))
    view.add_item(QuoteCancelButton(quote_id))
    embed = _customer_embed(
        quote, name, "Penawaran Harga",
        "Staff udah nentuin harga pesananmu. Klik **Lanjut Bayar** buat milih metode pembayaran, "
        "atau **Batalkan** kalau gak jadi.",
    )
    message = await _dm_user(bot, quote["user_id"], embed=embed, view=view)
    if message is None:
        return False
    await _delete_dm_message(bot, quote)           # DM lama (konfirmasi / penawaran sebelumnya)
    await quotes_q.set_dm_message(bot.db, quote_id, message.channel.id, message.id)
    return True


async def complete_quote(bot, quote_id: int, order_id: int) -> None:
    """Dipanggil begitu order dari penawaran ini jadi."""
    if not await quotes_q.mark_ordered(bot.db, quote_id, order_id):
        return
    quote = await quotes_q.get(bot.db, quote_id)
    await refresh_staff_message(bot, quote_id)
    await _delete_dm_message(bot, quote)
    await quotes_q.set_dm_message(bot.db, quote_id, None, None)


# ============================================================================
# TOMBOL STAFF
# ============================================================================

class PriceModal(discord.ui.Modal):
    def __init__(self, quote_id: int, current: float | None, note: str | None) -> None:
        super().__init__(title="Set Harga Pesanan")
        self.quote_id = quote_id
        self.price = discord.ui.TextInput(
            label="Harga pesanan", required=True, max_length=20,
            placeholder="Contoh: 150000 atau 150.000",
            default=money_text(current) if current else None,
        )
        self.note = discord.ui.TextInput(
            label="Catatan buat customer (opsional)", style=discord.TextStyle.paragraph,
            required=False, max_length=300, default=note or None,
        )
        self.add_item(self.price)
        self.add_item(self.note)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        amount, error = custom_order.parse_price(str(self.price.value))
        if error:
            await _say(interaction, error)
            return
        note = str(self.note.value or "").strip() or None
        if not await quotes_q.set_price(bot.db, self.quote_id, amount, note):
            await _say(interaction, "Permintaan ini udah gak bisa diubah (udah jadi order, ditolak, atau dibatalin).")
            await refresh_staff_message(bot, self.quote_id)
            return
        await refresh_staff_message(bot, self.quote_id)
        delivered = await send_price_dm(bot, self.quote_id)
        quote = await quotes_q.get(bot.db, self.quote_id)
        price_text = format_price(amount, quote["currency_label"] or "")
        if delivered:
            await _say(interaction, f"Harga **{price_text}** dikirim ke customer lewat DM.", ok=True)
        else:
            await _say(
                interaction,
                f"Harga **{price_text}** tersimpan, tapi DM ke customer gagal (DM-nya ditutup). "
                "Hubungi customer manual, lalu set harga lagi buat ngirim ulang.",
            )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception("Error di modal set harga penawaran.", exc_info=error)
        await _say(interaction, "Ada yang error pas nyimpen harga. Coba lagi ya.")


class QuotePriceButton(discord.ui.DynamicItem[discord.ui.Button], template=r"noctra_quote_price:(?P<qid>\d+)"):
    def __init__(self, quote_id: int, *, repricing: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                label="Ubah Harga" if repricing else "Set Harga", emoji="\U0001F4B0", style=_STYLE.success,
                custom_id=f"noctra_quote_price:{quote_id}",
            )
        )
        self.quote_id = quote_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(int(match["qid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await is_staff(interaction):
            await _say(interaction, "Kamu butuh izin staff buat nentuin harga.")
            return
        quote = await quotes_q.get(interaction.client.db, self.quote_id)  # type: ignore[attr-defined]
        if quote is None or quote["status"] not in ("pending", "priced"):
            await _say(interaction, "Permintaan ini udah gak bisa diubah (udah jadi order, ditolak, atau dibatalin).")
            return
        await interaction.response.send_modal(PriceModal(self.quote_id, quote["price"], quote["note"]))


class QuoteRejectButton(discord.ui.DynamicItem[discord.ui.Button], template=r"noctra_quote_reject:(?P<qid>\d+)"):
    def __init__(self, quote_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Tolak", emoji="\u2716\uFE0F", style=_STYLE.danger, custom_id=f"noctra_quote_reject:{quote_id}"
            )
        )
        self.quote_id = quote_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(int(match["qid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await is_staff(interaction):
            await _say(interaction, "Kamu butuh izin staff buat nolak permintaan.")
            return
        quote_id = self.quote_id

        async def submit(inter: discord.Interaction, reason: str) -> None:
            bot = inter.client
            quote = await quotes_q.get(bot.db, quote_id)
            if not await quotes_q.transition(bot.db, quote_id, "rejected", ("pending", "priced")):
                await _say(inter, "Permintaan ini udah gak bisa ditolak (udah jadi order atau dibatalin).")
                return
            if reason:
                await bot.db.execute("UPDATE custom_quotes SET note = ? WHERE id = ?", (reason, quote_id))
            quote = await quotes_q.get(bot.db, quote_id)
            await _delete_dm_message(bot, quote)
            await quotes_q.set_dm_message(bot.db, quote_id, None, None)
            await refresh_staff_message(bot, quote_id)
            name = await _product_name(bot.db, quote)
            await _dm_user(
                bot, quote["user_id"],
                embed=_customer_embed(
                    quote, name, "Permintaan Ditolak",
                    "Maaf, permintaanmu belum bisa diproses." + (f"\nAlasan: {reason}" if reason else ""),
                ),
            )
            await _say(inter, "Permintaan ditolak dan customer dikabarin.", ok=True)

        await interaction.response.send_modal(ReasonModal("Tolak Permintaan", submit))


# ============================================================================
# TOMBOL CUSTOMER
# ============================================================================

class QuotePayButton(discord.ui.DynamicItem[discord.ui.Button], template=r"noctra_quote_pay:(?P<qid>\d+)"):
    def __init__(self, quote_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Lanjut Bayar", emoji="\U0001F4B3", style=_STYLE.success, custom_id=f"noctra_quote_pay:{quote_id}"
            )
        )
        self.quote_id = quote_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(int(match["qid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        from bot.ui.views import proceed_to_payment

        db = interaction.client.db  # type: ignore[attr-defined]
        quote = await quotes_q.get(db, self.quote_id)
        if quote is None or quote["user_id"] != interaction.user.id:
            await _say(interaction, "Penawaran ini bukan punyamu.")
            return
        if quote["status"] != "priced" or quote["price"] is None:
            await _say(interaction, "Penawaran ini udah gak berlaku (dibayar, dibatalin, atau lagi diproses).")
            return
        cfg = await custom_order.active(db)
        if cfg is None or cfg.product_id != quote["product_id"]:
            await _say(interaction, "Fitur ini lagi gak tersedia. Hubungi staff ya.")
            return
        request = checkout_util.CheckoutRequest(
            ((cfg.product_id, 1),),
            overrides=((cfg.product_id, float(quote["price"])),),
            quote_id=self.quote_id,
        )
        # Alur pembayaran BIASA (pilih metode -> order dibuat). Pesan penawaran
        # ini tetap ada sampai order jadi, jadi kalau batal milih bisa diklik lagi.
        await proceed_to_payment(interaction, request, quotes_q.fields_of(quote))


class QuoteCancelButton(discord.ui.DynamicItem[discord.ui.Button], template=r"noctra_quote_cancel:(?P<qid>\d+)"):
    def __init__(self, quote_id: int) -> None:
        super().__init__(
            discord.ui.Button(
                label="Batalkan", emoji="\u2716\uFE0F", style=_STYLE.secondary, custom_id=f"noctra_quote_cancel:{quote_id}"
            )
        )
        self.quote_id = quote_id

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match, /):
        return cls(int(match["qid"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        quote = await quotes_q.get(bot.db, self.quote_id)
        if quote is None or quote["user_id"] != interaction.user.id:
            await _say(interaction, "Permintaan ini bukan punyamu.")
            return
        if not await quotes_q.transition(bot.db, self.quote_id, "cancelled", ("pending", "priced")):
            await _say(interaction, "Permintaan ini udah gak bisa dibatalin (lagi dibayar atau udah jadi order).")
            return
        await refresh_staff_message(bot, self.quote_id)
        await interaction.response.edit_message(
            embed=embeds.success_embed(f"Permintaan **#{self.quote_id}** dibatalin."), view=None
        )
        await quotes_q.set_dm_message(bot.db, self.quote_id, None, None)


QUOTE_ITEMS = (QuotePriceButton, QuoteRejectButton, QuotePayButton, QuoteCancelButton)
