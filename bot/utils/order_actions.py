"""
Logic transisi status order yang dipakai bareng: notifikasi DM ke customer,
restock stok pas cancel/refund, dan mulai prompt review button-only pas
order selesai.

Dipakai BARENG sama command admin `/order` dan tombol OrderActionButton di
channel order-log, jadi customer dapet notifikasi yang sama persis gak
peduli staff pake cara yang mana buat update order.
"""

from __future__ import annotations

from datetime import datetime, timezone

import discord

from bot.core.logger import logger
from bot.database.queries import cards as cards_q
from bot.database.queries import category_types as category_types_q
from bot.database.queries import orders as orders_q
from bot.database.queries import payments as payments_q
from bot.database.queries import products as products_q
from bot.database.queries import reviews as reviews_q
from bot.ui import components, embeds
from bot.utils import activity_log, order_chat
from bot.utils.helpers import RuntimeSettings, format_price, is_video_url


def _display_name(actor: discord.abc.User | None) -> str | None:
    """Nama staff yang enak dibaca customer/staff lain -- pake nickname
    server kalau ada (display_name), fallback ke username biasa. None
    kalau emang gak ada actor (misal dipanggil sistem tanpa staff)."""
    if actor is None:
        return None
    return getattr(actor, "display_name", None) or str(actor)


async def _notify_customer(
    bot,
    user_id: int,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    *,
    order_id: int | None = None,
    track: bool = False,
    layout: discord.ui.LayoutView | None = None,
) -> bool:
    """Kirim DM ke customer. Kalau `track` True (dan `order_id` diisi),
    pesan yang terkirim dicatat di order_dm_messages biar bisa dihapus
    belakangan -- dipakai buat pesan kerja yang sementara (notif status,
    prompt review, balesan staff), beda sama invoice final yang emang
    didesain buat nempel permanen.

    `layout` dipake buat pesan Components V2 (LayoutView) -- gak bisa
    dicampur sama `embed`/`view` biasa dalam satu pesan Discord, jadi kalau
    `layout` diisi, itu satu-satunya yang dikirim."""
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        if layout is not None:
            sent = await user.send(view=layout)
        elif view is not None:
            sent = await user.send(embed=embed, view=view)
        else:
            sent = await user.send(embed=embed)
        if track and order_id is not None:
            await orders_q.add_dm_message(bot.db, order_id, sent.channel.id, sent.id)
        return True
    except discord.HTTPException:
        logger.warning("Gagal DM user %s -- kemungkinan DM-nya ditutup.", user_id)
        return False


async def send_message_to_customer(
    bot, user_id: int, embed: discord.Embed, order_id: int | None = None,
    actor: discord.abc.User | None = None,
) -> bool:
    """Wrapper publik buat staff kirim DM ke customer (dipakai /order message
    dan tombol Reply di order-log). Ke-track buat dibersihin belakangan
    kalau `order_id` diisi, sama kayak pesan checkout lainnya.

    Kalau `actor` diisi: embed-nya dikasih footer nama staff yang bales
    (biar CUSTOMER tau siapa yang nanganin dia), DAN baris balasannya
    ikut ditambahin ke panel chat order ini di channel order-log (lihat
    bot.utils.order_chat) -- biar STAFF LAIN di channel itu juga tau
    siapa yang udah bales apa, gak cuma yang klik doang."""
    actor_display = _display_name(actor)
    if actor_display:
        embed.set_footer(text=f"Dibales oleh staff {actor_display}")

    sent = await _notify_customer(bot, user_id, embed, order_id=order_id, track=True)

    if sent and order_id is not None and actor_display:
        try:
            line = f"**[{order_chat.now_str()}] Staff {actor_display} (balasan):** {embed.description or '*(lampiran)*'}"
            customer = bot.get_user(user_id) or await bot.fetch_user(user_id)
            await order_chat.append_and_refresh(bot, order_id, customer, line)
        except Exception:  # noqa: BLE001
            # Balesan ke customer udah KETERKIRIM (sent=True) -- gagal
            # nge-update panel chat di sini gak boleh bikin caller nganggep
            # balesannya gagal, jadi diem-diem doang, gak di-raise ulang.
            logger.warning("Gagal update panel chat abis staff bales order #%s.", order_id)

    return sent


async def forward_to_staff(
    bot, order_id: int, user: discord.abc.User, content: str, attachment_urls: list[str]
) -> bool:
    """Neruskan DM customer (bisa teks biasa, screenshot bukti bayar,
    ATAU video -- misal nunjukin kendala produk) ke SATU panel chat per
    order di channel order-log (lihat bot.utils.order_chat) -- di-edit
    in-place tiap ada pesan baru, BUKAN bikin pesan baru tiap kali, biar
    channel order-log gak kebanjiran notif buat obrolan yang sama. Return
    False kalau channel order-log belum diatur SAMA SEKALI (belum pernah
    ada panel buat order ini)."""
    db = bot.db
    image_urls = [u for u in attachment_urls if not is_video_url(u)]
    video_urls = [u for u in attachment_urls if is_video_url(u)]

    if image_urls:
        # Cuma gambar PERTAMA yang PERNAH dikirim customer buat order ini
        # yang disimpen sebagai bukti bayar -- dicek dulu order-nya udah
        # punya proof apa belum SEBELUM nimpa. Ini yang dipake belakangan
        # buat notif "Testi Money" pas order completed (lihat
        # mark_completed). SEBELUMNYA baris ini jalan tiap ada gambar
        # baru masuk tanpa dicek dulu, jadi kalau customer kirim bukti
        # transfer LALU kirim gambar lain (obrolan biasa), Testi Money
        # malah ngambil gambar TERAKHIR, bukan bukti transfernya.
        order = await orders_q.get_order(db, order_id)
        if order and not order["payment_proof_url"]:
            await orders_q.set_payment_proof_url(db, order_id, image_urls[0])

    text = content if content else "*(lampiran, gak ada teks)*"
    line = f"**[{order_chat.now_str()}] {user.display_name}:** {text}"
    if len(image_urls) > 1:
        line += f"\n-# (+{len(image_urls) - 1} gambar lain di pesan yang sama)"

    ok = await order_chat.append_and_refresh(
        bot, order_id, user, line, image_url=image_urls[0] if image_urls else None
    )

    # Video tetep dikirim sebagai pesan TERPISAH (content polos, bukan
    # embed.set_image() yang gak bisa nerima video) -- Discord otomatis
    # nge-render player video-nya sendiri dari link mentah kayak gini,
    # staff tinggal klik play langsung di channel. Ini SENGAJA di luar
    # panel chat yang di-edit, soalnya videonya sendiri butuh pesannya
    # sendiri buat bisa diputer.
    if video_urls:
        runtime = RuntimeSettings(db)
        log_channel_id = await runtime.order_log_channel_id()
        channel = bot.get_channel(log_channel_id) if log_channel_id else None
        if isinstance(channel, discord.TextChannel):
            for video_url in video_urls:
                try:
                    await channel.send(content=f"Video dari Order #{order_id} -- {user}:\n{video_url}")
                except discord.HTTPException:
                    logger.warning("Gagal forward video customer buat order #%s.", order_id)

    return ok


async def cleanup_dm_messages(bot, order_id: int) -> None:
    """Hapus pesan-pesan checkout (ringkasan order, instruksi bayar, dst)
    yang NOCTRA kirim ke DM customer buat order ini, biar order yang udah
    selesai gak numpuk terus di riwayat DM mereka. Pake channel_id +
    message_id langsung (get_partial_message) daripada nyimpen objek pesan
    aslinya, soalnya ini bisa jalan berhari-hari setelah pesannya dikirim --
    jauh lewat masa berlaku webhook token apapun."""
    db = bot.db
    tracked = await orders_q.list_dm_messages(db, order_id)
    for row in tracked:
        try:
            channel = bot.get_channel(row["channel_id"]) or await bot.fetch_channel(row["channel_id"])
            await channel.get_partial_message(row["message_id"]).delete()
        except discord.HTTPException:
            pass  # udah kehapus, DM ketutup, atau kelamaan -- aman diabaikan
    await orders_q.clear_dm_messages(db, order_id)


async def _post_purchase_announcement(bot, order, product) -> None:
    """Posting kartu publik "Si X baru aja beli Y" ke channel purchase-feed
    yang diatur -- lihat /settings purchase_feed_channel. Diem-diem gak
    ngapa-ngapain kalau channel-nya belum diatur atau customer-nya gak bisa
    ditemuin; ini cuma pemanis, bukan sesuatu yang boleh nge-block atau
    gagalin alur completion order yang sesungguhnya."""
    if not product:
        return

    db = bot.db
    runtime = RuntimeSettings(db)
    channel_id = await runtime.purchase_feed_channel_id()
    if not channel_id:
        return

    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        user = bot.get_user(order["user_id"]) or await bot.fetch_user(order["user_id"])
        buyer_display = user.display_name
        buyer_avatar_url = user.display_avatar.url
    except discord.HTTPException:
        buyer_display = f"User {order['user_id']}"
        buyer_avatar_url = None

    category_type = await category_types_q.get_category_type(db, product["category_type_id"])
    embed = embeds.purchase_announcement_embed(buyer_display, buyer_avatar_url, product, category_type, order)

    try:
        await channel.send(embed=embed)
    except discord.HTTPException:
        logger.exception("Gagal posting pengumuman pembelian buat order #%s.", order["id"])


async def _notify_testi_proof(bot, order, product) -> None:
    """Notifikasi internal ke staff pake bukti transfer yang customer
    kirim duluan pas checkout (URL-nya disimpen forward_to_staff() di
    atas) -- dikirim ke /settings testi_proof_channel begitu order
    ditandain completed. SENGAJA gak nyangkut ke review sama sekali (beda
    dari review_card_container yang nunggu customer submit + staff
    approve review dulu), biar gak dobel kayak sebelumnya. Diem-diem gak
    ngapa-ngapain kalau proof-nya emang gak ada (order completed tanpa DM
    bukti bayar, misal staff mark paid manual) atau channel-nya belum
    diatur -- ini pemanis, bukan sesuatu yang boleh nge-block alur
    completion order."""
    proof_url = order["payment_proof_url"]
    if not proof_url or not product:
        return

    db = bot.db
    runtime = RuntimeSettings(db)
    channel_id = await runtime.testi_proof_channel_id()
    if not channel_id:
        return
    channel = bot.get_channel(channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    try:
        user = bot.get_user(order["user_id"]) or await bot.fetch_user(order["user_id"])
        buyer_display = user.mention
    except discord.HTTPException:
        buyer_display = f"User {order['user_id']}"

    price_text = format_price(order["total_price"], order["currency_label"])
    container = components.testi_proof_container(
        buyer_display=buyer_display,
        product_name=product["name"],
        price_text=price_text,
        testi_number=order["id"],
        photo_url=proof_url,
        emoji_title=await runtime.testi_proof_emoji_title(),
        emoji_buyer=await runtime.testi_proof_emoji_buyer(),
        emoji_product=await runtime.testi_proof_emoji_product(),
        emoji_price=await runtime.testi_proof_emoji_price(),
        emoji_testi=await runtime.testi_proof_emoji_testi(),
    )
    try:
        # allowed_mentions=none() SENGAJA -- buyer_display pake user.mention
        # biar tampil kayak chip mention di referensi, tapi ini notif ke
        # STAFF, jadi gak boleh ikut nge-ping customer-nya di channel
        # internal ini.
        await channel.send(
            view=components.NoctraLayout(container, timeout=None),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.HTTPException:
        logger.exception("Gagal posting notifikasi Testi Money buat order #%s.", order["id"])


async def mark_paid(bot, order_id: int, actor: discord.abc.User | None = None) -> tuple[bool, str]:
    db = bot.db
    order = await orders_q.get_order(db, order_id)
    if not order:
        return False, "Order gak ketemu."

    await orders_q.set_payment_status(db, order_id, "paid")
    if order["status"] == "pending":
        await orders_q.set_order_status(db, order_id, "processing")

    actor_display = _display_name(actor)
    approval_text = f"Order kamu #{order_id} udah **disetujui** oleh staff **{actor_display}** dan lagi diproses." \
        if actor_display else f"Order kamu #{order_id} udah ditandain **lunas** dan lagi diproses."

    await _notify_customer(
        bot,
        order["user_id"],
        embeds.success_embed(approval_text),
        order_id=order_id,
        track=True,
    )
    await activity_log.log_activity(
        bot, actor, "Order Ditandain Lunas", f"Order #{order_id} ditandain **lunas**."
    )
    return True, f"Order #{order_id} ditandain lunas."


async def mark_completed(bot, order_id: int, actor: discord.abc.User | None = None) -> tuple[bool, str]:
    db = bot.db
    order = await orders_q.get_order(db, order_id)
    if not order:
        return False, "Order gak ketemu."

    runtime = RuntimeSettings(db)

    await orders_q.set_order_status(db, order_id, "completed")
    if order["payment_status"] != "paid":
        # Order yang selesai otomatis berarti udah lunas -- tanpa ini,
        # staff yang klik "Mark Completed" tanpa klik "Mark Paid" duluan
        # bakal bikin payment_status nyangkut di "pending" selamanya, yang
        # diem-diem ngeblokir tombol review customer (butuh dua-duanya).
        await orders_q.set_payment_status(db, order_id, "paid")

    # Bersihin semua pesan kerja sementara buat order ini (alur checkout,
    # notif "udah lunas", balesan staff yang ada) sebelum kirim invoice
    # permanen -- biar invoice jadi awal yang bersih dari sisa DM customer,
    # bukan ketimbun di bawah yang lain.
    await cleanup_dm_messages(bot, order_id)

    product = await products_q.get_product(db, order["product_id"])
    product_name = product["name"] if product else "pembelian kamu"
    payment = (
        await payments_q.get_payment_method(db, order["payment_method_id"])
        if order["payment_method_id"]
        else None
    )

    # Ambil avatar bot langsung -- jadi kalau icon bot diganti, invoice ini
    # otomatis ikut sync tanpa perlu setting manual apapun.
    bot_avatar_url = bot.user.display_avatar.url if bot.user else None
    invoice_layout = components.invoice_view(order, product, payment, bot_avatar_url=bot_avatar_url)
    # Gak di-track -- invoice ini emang didesain buat nempel permanen
    # sebagai bukti pembelian customer, beda sama pesan lain di alur ini.
    await _notify_customer(bot, order["user_id"], layout=invoice_layout)

    existing_review = await reviews_q.get_review_by_order(db, order_id)
    if not existing_review:
        # Import ditunda: bot.ui.views ngimport module ini di level atas
        # (buat OrderActionButton), jadi kalau di-import balik di sini di
        # level module bakal circular. Pas fungsi ini beneran jalan, views
        # udah ke-load penuh, jadi import lazy ini aman dan murah.
        from bot.ui.views import ReviewStartButton

        review_view = discord.ui.View(timeout=None)
        review_view.add_item(ReviewStartButton(order_id))
        review_embed = embeds.info_embed(
            "Gimana Belanjanya?",
            f"Kasih tau orang lain gimana pendapat kamu soal **{product_name}**. "
            "Klik di bawah buat kasih rating -- gak perlu command apa-apa.",
        )
        # Di-track: begitu customer beneran submit review, prompt ini (dan
        # tombolnya) otomatis dibersihin -- lihat RatingButton di
        # bot.ui.views.
        await _notify_customer(bot, order["user_id"], review_embed, review_view, order_id=order_id, track=True)

    # Posting pengumuman pembelian ke channel purchase-feed, kalau diatur.
    try:
        await _post_purchase_announcement(bot, order, product)
    except Exception:  # noqa: BLE001
        logger.warning("Pengumuman pembelian gagal diem-diem abis order #%s.", order_id)

    # Notif "Testi Money" ke staff pake bukti transfer yang udah kesimpen,
    # kalau ada. Sama-sama non-blocking kayak pengumuman pembelian di atas.
    try:
        await _notify_testi_proof(bot, order, product)
    except Exception:  # noqa: BLE001
        logger.warning("Notifikasi Testi Money gagal diem-diem abis order #%s.", order_id)

    # Kasih Noctoins + Server Points kalau order ini dibayar pake Kartu
    # NOCTRA (BUKAN transfer manual) -- sesuai desain fitur kartu digital.
    # Dihitung dari total_price yang BENERAN kebayar (abis potongan
    # Noctoins kalau dipake), bukan harga sebelum diskon, biar gak ada
    # celah "pake Noctoins buat diskon gede tapi tetep dapet Noctoins dari
    # harga asli". CATATAN: kalau order yang UDAH completed ini nanti
    # di-refund/cancel, Noctoins/Points yang kadung dikasih di sini GAK
    # ditarik balik -- lihat cancel_order/refund_order di bawah.
    if order["paid_with_credit"]:
        try:
            rate = await runtime.card_noctoin_rate()
            if rate > 0:
                earned = int(order["total_price"] // rate)
                if earned > 0:
                    await cards_q.add_rewards(db, order["user_id"], earned, earned)
        except Exception:  # noqa: BLE001
            logger.warning("Ngasih Noctoins/Server Points gagal diem-diem abis order #%s.", order_id)

    # Refresh gambar leaderboard -- import lazy, fire-and-forget.
    try:
        from bot.utils.leaderboard import refresh_leaderboard
        await refresh_leaderboard(bot)
    except Exception:  # noqa: BLE001
        logger.warning("Refresh leaderboard gagal diem-diem abis order #%s.", order_id)

    await activity_log.log_activity(
        bot, actor, "Order Ditandain Selesai", f"Order #{order_id} ditandain **selesai**."
    )
    return True, f"Order #{order_id} ditandain selesai."


async def cancel_order(bot, order_id: int, reason: str | None, actor: discord.abc.User | None = None) -> tuple[bool, str]:
    db = bot.db
    order = await orders_q.get_order(db, order_id)
    if not order:
        return False, "Order gak ketemu."

    await orders_q.set_order_status(db, order_id, "cancelled")
    await orders_q.set_payment_status(db, order_id, "cancelled")
    if order["stock_reserved"]:
        await products_q.adjust_stock(db, order["product_id"], 1)
        await orders_q.clear_stock_reserved(db, order_id)

    # Order yang dibayar pake Kartu NOCTRA udah LANGSUNG lunas (saldo
    # dipotong di tempat pas checkout) -- beda dari pembayaran manual yang
    # "batal" ya emang gak pernah beneran bayar. Di sini duitnya udah
    # ke-ambil, jadi WAJIB dibalikin. Noctoins yang kepake juga dibalikin
    # (bukan add_rewards -- itu buat ngasih reward baru, ini cuma
    # ngembaliin yang sempet kepake, server_points SENGAJA gak ikut).
    if order["paid_with_credit"]:
        try:
            await cards_q.add_credit(db, order["user_id"], order["total_price"])
            if order["noctoins_used"]:
                await cards_q.add_noctoins(db, order["user_id"], order["noctoins_used"])
        except Exception:  # noqa: BLE001
            logger.warning("Refund saldo Credit gagal diem-diem abis batalin order #%s.", order_id)

    text = f"Order kamu #{order_id} udah **dibatalin**."
    if reason:
        text += f"\nAlasan: {reason}"
    await _notify_customer(bot, order["user_id"], embeds.error_embed(text))

    # Jaga leaderboard tetep akurat langsung saat itu juga. get_top_spenders
    # emang udah nge-filter yang bukan status='completed', tapi kalau order
    # ini sempet ditandain selesai sebelumnya dan baru sekarang dibatalin,
    # gambar leaderboard yang udah keposting gak bakal ngedrop spend-nya
    # sampe ada yang trigger refresh -- jadi langsung trigger di sini aja.
    try:
        from bot.utils.leaderboard import refresh_leaderboard
        await refresh_leaderboard(bot)
    except Exception:  # noqa: BLE001
        logger.warning("Refresh leaderboard gagal diem-diem abis batalin order #%s.", order_id)

    log_text = f"Order #{order_id} dibatalin."
    if reason:
        log_text += f"\nAlasan: {reason}"
    await activity_log.log_activity(bot, actor, "Order Dibatalin", log_text)
    return True, f"Order #{order_id} dibatalin."


async def refund_order(bot, order_id: int, reason: str | None, actor: discord.abc.User | None = None) -> tuple[bool, str]:
    db = bot.db
    order = await orders_q.get_order(db, order_id)
    if not order:
        return False, "Order gak ketemu."

    await orders_q.set_order_status(db, order_id, "refunded")
    if order["stock_reserved"]:
        await products_q.adjust_stock(db, order["product_id"], 1)
        await orders_q.clear_stock_reserved(db, order_id)

    # Sama kayak cancel_order -- kalau ini order Kartu NOCTRA, duitnya udah
    # ke-ambil di depan, jadi WAJIB dibalikin (plus Noctoins yang kepake).
    if order["paid_with_credit"]:
        try:
            await cards_q.add_credit(db, order["user_id"], order["total_price"])
            if order["noctoins_used"]:
                await cards_q.add_noctoins(db, order["user_id"], order["noctoins_used"])
        except Exception:  # noqa: BLE001
            logger.warning("Refund saldo Credit gagal diem-diem abis refund order #%s.", order_id)

    text = f"Order kamu #{order_id} udah **di-refund**."
    if reason:
        text += f"\nAlasan: {reason}"
    await _notify_customer(bot, order["user_id"], embeds.error_embed(text))

    # Alasan sama kayak cancel_order -- refund bisa aja kejadian abis order
    # udah sempet selesai dan kehitung, jadi langsung refresh aja.
    try:
        from bot.utils.leaderboard import refresh_leaderboard
        await refresh_leaderboard(bot)
    except Exception:  # noqa: BLE001
        logger.warning("Refresh leaderboard gagal diem-diem abis refund order #%s.", order_id)

    log_text = f"Order #{order_id} di-refund."
    if reason:
        log_text += f"\nAlasan: {reason}"
    await activity_log.log_activity(bot, actor, "Order Di-refund", log_text)
    return True, f"Order #{order_id} di-refund."
