"""
Command staff: /manual -- versi MANUAL dari payment, testi, dan rating buat
order yang diproses lewat ticket (produk di luar panel shop), tampilannya
SAMA PERSIS kayak yang dikirim bot di alur checkout biasa. Bedanya cuma
datanya diisi staff lewat parameter command, bukan diambil dari database
order/produk.

  /manual payment  produk:<> harga:<> [customer] [metode] [catatan] [mata_uang]
        Pesan pembayaran di channel ini: ringkasan order + instruksi/QR
        metode bayar + petunjuk kirim bukti. `metode` kosong = tampilin SEMUA
        metode aktif (maks 5). Metode diambil dari yang udah diatur di bot.
  /manual testi    produk:<> harga:<> foto:<> [pembeli | nama_pembeli] [channel]
        Kartu "TESTI MONEY" ke channel testi (/settings testi_proof_channel).
  /manual rating   produk:<> bintang:<1-5> [komentar] [foto] [pembeli |
        nama_pembeli] [terverifikasi] [channel]
        Kartu review publik ke channel review (/settings reviews_channel).
  /manual riwayat  [jenis]  -- 10 kartu manual terakhir (siapa yang bikin).

Kartu manual dikasih nomor sendiri berawalan M (#M1, #M2, ...) per jenis
biar gak ketuker sama nomor order/review asli. Foto di-upload ULANG ke pesan
kartunya (bukan cuma ditautin), jadi gak kedaluwarsa kayak link attachment
Discord biasa.

Catatan: kartu manual BUKAN order di database -- gak masuk leaderboard, tier
loyalitas, atau statistik staff.
"""

from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.core.logger import logger
from bot.database.queries import manual_entries as manual_q
from bot.ui import components, embeds
from bot.utils.helpers import RuntimeSettings, format_price
from bot.utils.permissions import staff_only

MAX_PAYMENT_METHODS_SHOWN = 5
ALLOWED_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _safe(text: str) -> str:
    """Teks bebas dari staff -- markdown & mention nyasar di-escape."""
    return discord.utils.escape_mentions(discord.utils.escape_markdown(text))


def _is_image(attachment: discord.Attachment) -> bool:
    content_type = (attachment.content_type or "").lower()
    return content_type.startswith("image/") or attachment.filename.lower().endswith(ALLOWED_IMAGE_EXTENSIONS)


def _fake_order_row(number: int, amount: float, currency: str) -> dict:
    """Baris order tiruan buat embeds.order_summary_embed() -- bentuk sama
    kayak baris database asli, dipake biar tampilannya identik sama bot."""
    return {
        "id": f"M{number}", "status": "pending", "payment_status": "pending", "total_price": amount,
        "currency_label": currency, "paid_with_credit": 0,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


class ManualCog(commands.Cog):
    manual_group = app_commands.Group(
        name="manual", description="Versi manual payment, testi, dan rating buat order lewat ticket.", guild_only=True
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        await manual_q.ensure_tables(self.bot.db)

    # -- Util -------------------------------------------------------------------------

    async def _prepare_photo(
        self, interaction: discord.Interaction, attachment: discord.Attachment
    ) -> tuple[discord.File | None, str | None, str | None]:
        """Return (file, url_buat_kartu, pesan_error). Foto di-upload ulang
        ke pesan kartu (url `attachment://nama`) supaya gak kedaluwarsa; kalau
        gagal diambil, jatuh ke link aslinya."""
        if not _is_image(attachment):
            return None, None, "File harus berupa gambar (png/jpg/jpeg/webp/gif)."
        limit = interaction.guild.filesize_limit if interaction.guild else 10 * 1024 * 1024
        if attachment.size > limit:
            return None, None, f"Ukuran gambar kegedean (maks {limit // (1024 * 1024)} MB di server ini)."
        try:
            file = await attachment.to_file(filename=_clean_filename(attachment.filename))
        except discord.HTTPException:
            return None, attachment.url, None
        return file, f"attachment://{file.filename}", None

    async def _resolve_channel(
        self, interaction: discord.Interaction, override: discord.TextChannel | None, setting_name: str, setting_hint: str
    ) -> discord.TextChannel | None:
        if override is not None:
            return override
        channel_id = await getattr(RuntimeSettings(self.bot.db), setting_name)()
        channel = self.bot.get_channel(channel_id) if channel_id else None
        if isinstance(channel, discord.TextChannel):
            return channel
        await interaction.followup.send(
            embed=embeds.error_embed(
                f"Channel tujuan belum diatur. Atur lewat {setting_hint}, atau pilih channel langsung di parameter `channel`."
            ),
            ephemeral=True,
        )
        return None

    # -- /manual payment -------------------------------------------------------------------

    @manual_group.command(name="payment", description="Kirim instruksi pembayaran manual (ringkasan order + metode bayar) di channel ini.")
    @app_commands.describe(
        produk="Nama produk/jasa yang dipesan", harga="Total yang harus dibayar (angka)",
        customer="(opsional) Pembeli -- di-mention di pesan", metode="(opsional) Metode bayar; kosong = tampilkan semua metode aktif",
        catatan="(opsional) Catatan/detail order yang ikut ditampilkan", mata_uang="(opsional) Default: mata uang toko",
    )
    @staff_only()
    async def payment(
        self, interaction: discord.Interaction, produk: app_commands.Range[str, 2, 100],
        harga: app_commands.Range[float, 0.01, 1_000_000_000_000], customer: discord.Member | None = None,
        metode: str | None = None, catatan: app_commands.Range[str, 1, 300] | None = None,
        mata_uang: app_commands.Range[str, 2, 10] | None = None,
    ) -> None:
        db = self.bot.db
        await interaction.response.defer(ephemeral=True)

        if metode is not None:
            row = await manual_q.get_active_payment_method(db, int(metode)) if metode.isdigit() else None
            if row is None:
                await interaction.followup.send(embed=embeds.error_embed("Metode bayar itu gak ketemu atau lagi nonaktif."), ephemeral=True)
                return
            methods = [row]
        else:
            methods = list(await manual_q.list_active_payment_methods(db))[:MAX_PAYMENT_METHODS_SHOWN]
            if not methods:
                await interaction.followup.send(embed=embeds.error_embed("Belum ada metode pembayaran aktif yang diatur."), ephemeral=True)
                return

        currency = (mata_uang or await RuntimeSettings(db).default_currency()).upper()
        entry = await manual_q.create_entry(
            db, "payment", interaction.user.id, produk, customer_id=customer.id if customer else None,
            amount=float(harga), currency=currency, note=catatan,
        )

        field_values = [{"label": "Catatan", "field_type": "text", "value": catatan}] if catatan else None
        summary = embeds.order_summary_embed(
            _fake_order_row(entry["number"], float(harga), currency), {"name": produk},
            methods[0] if len(methods) == 1 else None, field_values,
        )
        reply_embeds = [summary]
        for method in methods:
            if method["instructions"] or method["image_url"]:
                reply_embeds.append(
                    embeds.info_embed(
                        f"Pembayaran -- {method['name']}",
                        method["instructions"] or "Scan QR code di bawah buat bayar.",
                        image_url=method["image_url"],
                    )
                )
        reply_embeds.append(
            embeds.info_embed(
                "Udah Bayar?",
                "Kalau udah bayar, kirim bukti bayarnya (screenshot juga oke) langsung di channel ini -- "
                "staff bakal cek dan konfirmasi secepatnya.",
            )
        )

        mention = customer.mention if customer else ""
        content = f"{mention} Order kamu udah dibuat! Ini detailnya:".strip()
        try:
            sent = await interaction.channel.send(
                content=content, embeds=reply_embeds,
                allowed_mentions=discord.AllowedMentions(users=[customer] if customer else False),
            )
        except discord.HTTPException:
            logger.exception("Gagal kirim pesan /manual payment.")
            await manual_q.delete_entry(db, entry["id"])
            await interaction.followup.send(embed=embeds.error_embed("Gagal kirim pesan pembayaran di channel ini."), ephemeral=True)
            return
        await manual_q.set_message(db, entry["id"], interaction.channel.id, sent.id)
        await interaction.followup.send(
            embed=embeds.success_embed(f"Pesan pembayaran **#M{entry['number']}** terkirim: {sent.jump_url}"), ephemeral=True
        )

    @payment.autocomplete("metode")
    async def _payment_method_autocomplete(self, interaction: discord.Interaction, current: str):
        rows = await manual_q.list_active_payment_methods(self.bot.db)
        needle = current.lower()
        return [
            app_commands.Choice(name=row["name"][:100], value=str(row["id"]))
            for row in rows if needle in row["name"].lower()
        ][:25]

    # -- /manual testi ------------------------------------------------------------------------

    @manual_group.command(name="testi", description="Kirim kartu TESTI MONEY manual (bukti transaksi) ke channel testi.")
    @app_commands.describe(
        produk="Nama produk/jasa", harga="Nominal transaksi (angka)", foto="Foto bukti transaksi/transfer",
        pembeli="(pilih salah satu) Pembeli yang ada di server", nama_pembeli="(pilih salah satu) Nama pembeli kalau bukan member server",
        channel="(opsional) Channel tujuan; default: channel testi di /settings", mata_uang="(opsional) Default: mata uang toko",
    )
    @staff_only()
    async def testi(
        self, interaction: discord.Interaction, produk: app_commands.Range[str, 2, 100],
        harga: app_commands.Range[float, 0, 1_000_000_000_000], foto: discord.Attachment,
        pembeli: discord.Member | None = None, nama_pembeli: app_commands.Range[str, 2, 60] | None = None,
        channel: discord.TextChannel | None = None, mata_uang: app_commands.Range[str, 2, 10] | None = None,
    ) -> None:
        if (pembeli is None) == (nama_pembeli is None):
            await interaction.response.send_message(
                embed=embeds.error_embed("Isi salah satu: `pembeli` (member server) ATAU `nama_pembeli` (teks)."), ephemeral=True
            )
            return
        db = self.bot.db
        await interaction.response.defer(ephemeral=True)
        target = await self._resolve_channel(interaction, channel, "testi_proof_channel_id", "`/settings testi_proof_channel`")
        if target is None:
            return
        file, photo_url, error = await self._prepare_photo(interaction, foto)
        if error:
            await interaction.followup.send(embed=embeds.error_embed(error), ephemeral=True)
            return

        runtime = RuntimeSettings(db)
        currency = (mata_uang or await runtime.default_currency()).upper()
        buyer_display = pembeli.mention if pembeli else _safe(nama_pembeli)
        entry = await manual_q.create_entry(
            db, "testi", interaction.user.id, produk, customer_id=pembeli.id if pembeli else None,
            customer_label=None if pembeli else nama_pembeli, amount=float(harga), currency=currency,
        )
        container = components.testi_proof_container(
            buyer_display=buyer_display, product_name=produk, price_text=format_price(float(harga), currency),
            testi_number=f"M{entry['number']}", photo_url=photo_url,
            emoji_title=await runtime.testi_proof_emoji_title(), emoji_buyer=await runtime.testi_proof_emoji_buyer(),
            emoji_product=await runtime.testi_proof_emoji_product(), emoji_price=await runtime.testi_proof_emoji_price(),
            emoji_testi=await runtime.testi_proof_emoji_testi(),
        )
        try:
            sent = await target.send(
                view=components.NoctraLayout(container, timeout=None), files=[file] if file else discord.utils.MISSING,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.exception("Gagal kirim kartu /manual testi.")
            await manual_q.delete_entry(db, entry["id"])
            await interaction.followup.send(embed=embeds.error_embed(f"Gagal kirim kartu testi ke {target.mention}."), ephemeral=True)
            return
        await manual_q.set_message(db, entry["id"], target.id, sent.id)
        await interaction.followup.send(
            embed=embeds.success_embed(f"Kartu testi **#M{entry['number']}** terkirim ke {target.mention}: {sent.jump_url}"), ephemeral=True
        )

    # -- /manual rating -----------------------------------------------------------------------

    @manual_group.command(name="rating", description="Kirim kartu review/rating manual ke channel review.")
    @app_commands.describe(
        produk="Nama produk/jasa yang direview", bintang="Rating 1-5", komentar="(opsional) Isi review",
        foto="(opsional) Foto review", pembeli="(pilih salah satu) Pembeli yang ada di server",
        nama_pembeli="(pilih salah satu) Nama pembeli kalau bukan member server",
        terverifikasi="Tampilkan badge 'Pembelian Terverifikasi' (default: ya)",
        channel="(opsional) Channel tujuan; default: channel review di /settings",
    )
    @staff_only()
    async def rating(
        self, interaction: discord.Interaction, produk: app_commands.Range[str, 2, 100],
        bintang: app_commands.Range[int, 1, 5], komentar: app_commands.Range[str, 1, 500] | None = None,
        foto: discord.Attachment | None = None, pembeli: discord.Member | None = None,
        nama_pembeli: app_commands.Range[str, 2, 60] | None = None, terverifikasi: bool = True,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if (pembeli is None) == (nama_pembeli is None):
            await interaction.response.send_message(
                embed=embeds.error_embed("Isi salah satu: `pembeli` (member server) ATAU `nama_pembeli` (teks)."), ephemeral=True
            )
            return
        db = self.bot.db
        await interaction.response.defer(ephemeral=True)
        target = await self._resolve_channel(interaction, channel, "reviews_channel_id", "`/settings reviews_channel`")
        if target is None:
            return
        file = photo_url = None
        if foto is not None:
            file, photo_url, error = await self._prepare_photo(interaction, foto)
            if error:
                await interaction.followup.send(embed=embeds.error_embed(error), ephemeral=True)
                return

        runtime = RuntimeSettings(db)
        comment = " ".join(komentar.split()) if komentar else None  # satu paragraf -- format kutipan kartu cuma nge-quote baris pertama
        entry = await manual_q.create_entry(
            db, "rating", interaction.user.id, produk, customer_id=pembeli.id if pembeli else None,
            customer_label=None if pembeli else nama_pembeli, rating=bintang, note=comment,
        )
        container = components.review_card_container(
            {"id": f"M{entry['number']}", "rating": bintang, "review_text": _safe(comment) if comment else None, "image_url": photo_url},
            {"name": produk},
            pembeli.mention if pembeli else _safe(nama_pembeli),
            emoji_title=await runtime.review_card_emoji_title(), emoji_user=await runtime.review_card_emoji_user(),
            emoji_product=await runtime.review_card_emoji_product(), emoji_rating=await runtime.review_card_emoji_rating(),
            emoji_star_filled=await runtime.review_card_emoji_star_filled(), emoji_star_empty=await runtime.review_card_emoji_star_empty(),
            emoji_message=await runtime.review_card_emoji_message(),
            author_avatar_url=pembeli.display_avatar.url if pembeli else None,
            banner_url=await runtime.review_banner_url(), verified=terverifikasi,
        )
        try:
            sent = await target.send(
                view=components.NoctraLayout(container, timeout=None), files=[file] if file else discord.utils.MISSING,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            logger.exception("Gagal kirim kartu /manual rating.")
            await manual_q.delete_entry(db, entry["id"])
            await interaction.followup.send(embed=embeds.error_embed(f"Gagal kirim kartu review ke {target.mention}."), ephemeral=True)
            return
        await manual_q.set_message(db, entry["id"], target.id, sent.id)
        await interaction.followup.send(
            embed=embeds.success_embed(f"Kartu review **#M{entry['number']}** terkirim ke {target.mention}: {sent.jump_url}"), ephemeral=True
        )

    # -- /manual riwayat -------------------------------------------------------------------------

    @manual_group.command(name="riwayat", description="10 kartu manual terakhir (siapa yang bikin).")
    @app_commands.describe(jenis="(opsional) Saring per jenis")
    @app_commands.choices(jenis=[app_commands.Choice(name=n.title(), value=n) for n in manual_q.KINDS])
    @staff_only()
    async def riwayat(self, interaction: discord.Interaction, jenis: app_commands.Choice[str] | None = None) -> None:
        rows = await manual_q.list_recent(self.bot.db, jenis.value if jenis else None, 10)
        if not rows:
            await interaction.response.send_message(embed=embeds.info_embed("Riwayat Manual", "Belum ada kartu manual."), ephemeral=True)
            return
        lines = []
        for row in rows:
            detail = row["product"] if row["kind"] != "rating" else f"{row['product']} ({row['rating']}/5)"
            amount = f" -- {format_price(row['amount'], row['currency'] or '')}" if row["amount"] is not None else ""
            who = f"<@{row['customer_id']}>" if row["customer_id"] else (_safe(row["customer_label"]) if row["customer_label"] else "-")
            link = f" [lihat](https://discord.com/channels/{interaction.guild_id}/{row['channel_id']}/{row['message_id']})" if row["message_id"] else ""
            lines.append(f"\u25b8 **{row['kind'].title()} #M{row['number']}** -- {_safe(detail)}{amount} -- {who} -- oleh <@{row['staff_id']}>{link}")
        await interaction.response.send_message(
            embed=embeds.info_embed("Riwayat Manual", "\n".join(lines)[:4000]), ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def _clean_filename(name: str) -> str:
    """Nama file aman buat `attachment://` (tanpa spasi/karakter aneh)."""
    stem, dot, ext = name.rpartition(".")
    base = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in (stem or name))[:40] or "foto"
    return f"{base}.{ext.lower()}" if dot and ext.isalnum() else f"{base}.png"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ManualCog(bot))
