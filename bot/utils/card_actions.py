"""
Logic approve/reject permintaan kartu digital (bikin baru / isi saldo) --
dipake CardRequestActionButton di channel /settings card_requests_channel.
Dipakai bareng kayak order_actions.py: satu tempat, jadi approve lewat
tombol atau (kalau nanti ada) command manual hasilnya konsisten.

Card ID itu SERIAL/REFERENSI doang buat kebutuhan support -- BUKAN
kredensial. Dibikin acak (bukan sequential) biar gak gampang ditebak, tapi
gak ada satupun aksi di bot ini (cek saldo, isi saldo) yang nerima ID
sebagai input buat otorisasi -- semuanya selalu ke-tie ke akun Discord yang
invoke command/tombolnya. Lihat bot.ui.views.CardPanelView.
"""

from __future__ import annotations

import secrets
import string
from io import BytesIO

import aiohttp
import discord
from PIL import Image

from bot.core.logger import logger
from bot.database.queries import cards as cards_q
from bot.ui import embeds
from bot.utils import activity_log
from bot.utils.card_image import generate_card_image
from bot.utils.helpers import RuntimeSettings, format_price

_CARD_ID_ALPHABET = string.ascii_uppercase + string.digits


async def _generate_unique_card_id(db) -> str:
    for _ in range(20):
        body = "".join(secrets.choice(_CARD_ID_ALPHABET) for _ in range(10))
        card_id = f"NCTR-{body[:5]}-{body[5:]}"
        if not await cards_q.card_id_exists(db, card_id):
            return card_id
    # Praktis gak mungkin kejadian (10 karakter dari alphabet 36 = ~3.6
    # kuadriliun kombinasi) -- kalau ini beneran ke-raise, ada bug lain,
    # bukan sial.
    raise RuntimeError("Gagal generate card_id unik abis 20 percobaan.")


async def _fetch_avatar(bot, user_id: int) -> tuple[Image.Image | None, discord.abc.User | None]:
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
    except discord.HTTPException:
        return None, None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                str(user.display_avatar.url), timeout=aiohttp.ClientTimeout(total=4)
            ) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    return Image.open(BytesIO(data)).convert("RGBA"), user
    except Exception:
        pass
    return None, user


async def build_card_file(bot, user_id: int, card) -> discord.File:
    """Generate gambar kartu buat user_id, siap kirim (discord.File) --
    dipake bareng abis approve (DM ke customer) dan tombol Cek Saldo, biar
    gak dobel logic fetch avatar + generate."""
    avatar, user = await _fetch_avatar(bot, user_id)
    username = user.display_name if user else f"User {user_id}"
    currency = await RuntimeSettings(bot.db).default_currency()
    buf = generate_card_image(
        username=username,
        avatar=avatar,
        credit_balance=card["credit_balance"],
        currency_label=currency,
        noctoins=card["noctoins"],
        server_points=card["server_points"],
    )
    return discord.File(buf, filename="noctra_card.png")


async def _send_card_dm(bot, user_id: int, card, extra_text: str) -> None:
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        file = await build_card_file(bot, user_id, card)
        await user.send(content=extra_text, file=file)
    except discord.HTTPException:
        logger.warning("Gagal DM kartu ke user %s.", user_id)


async def approve_request(bot, request_id: int, actor: discord.abc.User | None = None) -> tuple[bool, str]:
    db = bot.db
    request = await cards_q.get_request(db, request_id)
    if not request:
        return False, "Permintaan gak ketemu."
    if request["status"] != "pending":
        return False, f"Permintaan ini udah `{request['status']}`, gak bisa diproses lagi."

    runtime = RuntimeSettings(db)
    currency = await runtime.default_currency()

    if request["kind"] == "create":
        if await cards_q.get_card_by_user(db, request["user_id"]):
            await cards_q.set_request_status(db, request_id, "rejected")
            return False, "Customer ini udah punya kartu -- permintaan otomatis di-reject."

        card_id = await _generate_unique_card_id(db)
        credit = request["amount"] - request["admin_fee"]
        await cards_q.create_card(db, request["user_id"], card_id, credit)
        await cards_q.set_request_status(db, request_id, "approved")

        card = await cards_q.get_card_by_user(db, request["user_id"])
        await _send_card_dm(
            bot, request["user_id"], card,
            extra_text=(
                f"Kartu NOCTRA kamu berhasil dibuat! ID kartu kamu: **{card_id}**\n\n"
                "⚠️ **Simpen ID ini baik-baik dan JANGAN disebarin ke siapapun** -- "
                "ini cuma dipake staff buat bantuin kamu kalau ada kendala soal kartu ini, "
                "bukan buat login atau transaksi.\n\n"
                f"Saldo Credit awal kamu: **{format_price(credit, currency)}** "
                f"(dari deposit {format_price(request['amount'], currency)}, dipotong biaya admin "
                f"{format_price(request['admin_fee'], currency)})."
            ),
        )
        await activity_log.log_activity(
            bot, actor, "Kartu Baru Disetujui",
            f"Kartu baru buat <@{request['user_id']}> disetujui (ID `{card_id}`, Credit awal "
            f"{format_price(credit, currency)}).",
        )
        return True, f"Kartu baru buat <@{request['user_id']}> berhasil dibuat (ID `{card_id}`)."

    # kind == "topup"
    card = await cards_q.get_card_by_user(db, request["user_id"])
    if not card:
        await cards_q.set_request_status(db, request_id, "rejected")
        return False, "Customer ini belum punya kartu -- permintaan otomatis di-reject."

    await cards_q.add_credit(db, request["user_id"], request["amount"])
    await cards_q.set_request_status(db, request_id, "approved")
    card = await cards_q.get_card_by_user(db, request["user_id"])
    await _send_card_dm(
        bot, request["user_id"], card,
        extra_text=(
            f"Saldo Credit kamu berhasil ditambah **{format_price(request['amount'], currency)}**. "
            f"Saldo sekarang: **{format_price(card['credit_balance'], currency)}**."
        ),
    )
    await activity_log.log_activity(
        bot, actor, "Isi Saldo Disetujui",
        f"Saldo <@{request['user_id']}> ditambah {format_price(request['amount'], currency)} "
        f"(saldo sekarang {format_price(card['credit_balance'], currency)}).",
    )
    return True, f"Saldo <@{request['user_id']}> berhasil ditambah {format_price(request['amount'], currency)}."


async def reject_request(bot, request_id: int, reason: str | None, actor: discord.abc.User | None = None) -> tuple[bool, str]:
    db = bot.db
    request = await cards_q.get_request(db, request_id)
    if not request:
        return False, "Permintaan gak ketemu."
    if request["status"] != "pending":
        return False, f"Permintaan ini udah `{request['status']}`, gak bisa diproses lagi."

    await cards_q.set_request_status(db, request_id, "rejected")

    text = "Permintaan kartu/isi-saldo kamu ditolak staff."
    if reason:
        text += f"\nAlasan: {reason}"
    try:
        user = bot.get_user(request["user_id"]) or await bot.fetch_user(request["user_id"])
        await user.send(embed=embeds.error_embed(text))
    except discord.HTTPException:
        logger.warning("Gagal DM penolakan kartu ke user %s.", request["user_id"])

    log_text = f"Permintaan kartu `#{request_id}` dari <@{request['user_id']}> di-reject."
    if reason:
        log_text += f"\nAlasan: {reason}"
    await activity_log.log_activity(bot, actor, "Permintaan Kartu Di-reject", log_text)
    return True, f"Permintaan `#{request_id}` udah di-reject."
