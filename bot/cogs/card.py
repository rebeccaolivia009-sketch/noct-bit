"""
Dengerin DM customer buat nangkep bukti transfer permintaan kartu (bikin
baru / isi saldo) -- pola sama persis kayak bot.cogs.review_photo: modal
dulu (lihat CardPanelView di bot.ui.views), baru abis itu customer kirim
screenshot-nya sebagai pesan DM biasa, dan listener ini yang nangkep.

Approve/reject-nya sendiri ada di tombol CardRequestActionButton (lihat
bot.ui.views), yang manggil bot.utils.card_actions -- file ini cuma
ngurusin sisi "nangkep foto dari customer, terusin ke staff".

Order produk (bot.cogs.payment_proof) SELALU menang kalau customer
kebetulan punya order aktif yang nunggu bukti bayar BARENGAN sama
permintaan kartu yang nunggu bukti juga -- lihat komen di on_message.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from bot.database.queries import cards as cards_q
from bot.database.queries import orders as orders_q
from bot.ui import embeds
from bot.utils.helpers import RuntimeSettings, format_price


class CardCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is not None:
            return  # cuma perhatiin DM dari user asli

        db = self.bot.db
        request = await cards_q.get_awaiting_proof_request_for_user(db, message.author.id)
        if not request:
            return  # customer ini gak lagi diharapin ngirim bukti sekarang

        image_attachment = next(
            (a for a in message.attachments if (a.content_type or "").startswith("image/")), None
        )
        if not image_attachment:
            # Mereka lagi ngobrol soal hal lain -- jangan dianggep "gak ada
            # bukti" dan dilewatin, tunggu aja sampe ada gambar beneran.
            return

        # Kalau customer JUGA punya order produk aktif yang nunggu bukti
        # bayar, gambar ini AMBIGU -- bot.cogs.payment_proof punya listener
        # sendiri yang bakal nangkep gambar yang sama ini juga (Discord
        # dispatch on_message ke SEMUA listener, gak cuma satu). Order
        # produk menang duluan di sini (mundur, gak ikut diproses) biar gak
        # dobel notif ke dua channel staff sekaligus buat satu gambar yang
        # sama. Konsekuensinya: kalau customer emang niat kirim bukti buat
        # kartu tapi kebetulan masih punya order lama yang nyangkut belum
        # kebayar, mereka perlu beresin/batalin order itu dulu (atau minta
        # staff bantu manual) sebelum bukti kartu-nya kebaca di sini.
        active_orders = await orders_q.list_active_orders_for_user(db, message.author.id)
        if any(o["payment_status"] == "pending" for o in active_orders):
            return

        await cards_q.set_request_proof(db, request["id"], image_attachment.url)
        await message.channel.send(
            embed=embeds.success_embed(
                "Bukti transfer kamu udah diterima dan lagi nunggu staff approve. Sabar ya!"
            )
        )
        await self._forward_to_staff(request["id"])

    async def _forward_to_staff(self, request_id: int) -> None:
        db = self.bot.db
        runtime = RuntimeSettings(db)
        channel_id = await runtime.card_requests_channel_id()
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        request = await cards_q.get_request(db, request_id)
        if not request:
            return

        try:
            user = self.bot.get_user(request["user_id"]) or await self.bot.fetch_user(request["user_id"])
            user_display = f"{user} ({user.mention})"
        except discord.HTTPException:
            user_display = f"User {request['user_id']}"

        currency = await runtime.default_currency()
        kind_label = "Buat Kartu Baru" if request["kind"] == "create" else "Isi Saldo"
        lines = [
            f"**Customer:** {user_display}",
            f"**Jenis:** {kind_label}",
            f"**Nominal:** {format_price(request['amount'], currency)}",
        ]
        if request["kind"] == "create":
            lines.append(f"**Biaya Admin:** {format_price(request['admin_fee'], currency)}")
            lines.append(
                f"**Credit yang bakal masuk:** "
                f"{format_price(request['amount'] - request['admin_fee'], currency)}"
            )
        embed = embeds.info_embed(f"Permintaan Kartu #{request['id']}", "\n".join(lines))
        if request["proof_url"]:
            embed.set_image(url=request["proof_url"])

        # Import ditunda: bot.ui.views ngimport bot.utils.card_actions di
        # level atas, jadi kalau di-import balik di sini di level module
        # bakal circular. Pas fungsi ini beneran jalan, views udah ke-load
        # penuh, jadi import lazy ini aman -- pola sama kayak
        # order_actions.forward_to_staff().
        from bot.ui.views import CardRequestActionButton

        view = discord.ui.View(timeout=None)
        view.add_item(CardRequestActionButton("approve", request_id))
        view.add_item(CardRequestActionButton("reject", request_id))

        try:
            await channel.send(embed=embed, view=view)
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CardCog(bot))
