"""
Nerusin DM customer ke staff selama order mereka masih aktif (dari dibuat
sampe ditandain paid dan diproses, sampe akhirnya completed) -- ini yang
bikin "kirim bukti bayar kamu di sini" beneran nyampe ke orang yang perlu
liat, tanpa pernah harus buka channel ticket, dan ngebolehin customer tetep
chat staff soal order-nya bahkan abis ditandain paid.

Perilakunya:
  * Cuma DM channel yang diperhatiin (pesan di guild diabaikan).
  * Kalau customer punya pas satu order aktif, pesannya (teks + lampiran
    apapun) langsung diterusin ke channel order-log, ditandain sama order
    ID itu dan mention customer-nya.
  * Kalau lebih dari satu, mereka diminta milih order yang mana lewat
    Select menu dulu sebelum apapun diterusin -- ini fix beneran buat
    "yang beneran beli yang mana": tiap pesan yang diterusin jelas
    ke-tag ke satu order spesifik.
  * Kalau mereka punya nol order aktif (belum ada, atau udah completed),
    bot diem aja di sini (bukan chatbot DM serbaguna). Obrolan "gimana
    belanjanya" buat order yang udah completed ditangani terpisah sama
    listener review-photo, bukan yang ini.
  * Konfirmasi yang keliatan cuma dikirim balik kalau ada lampiran (kasus
    bukti bayar) biar gak ngebales tiap pesan di obrolan biasa.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from bot.database.queries import orders as orders_q
from bot.ui import embeds
from bot.ui.views import PendingOrderSelectView
from bot.utils import order_actions


class PaymentProofCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is not None:
            return  # cuma nerusin DM dari user asli

        db = self.bot.db
        active = await orders_q.list_active_orders_for_user(db, message.author.id)
        if not active:
            return

        attachment_urls = [a.url for a in message.attachments]

        if len(active) > 1:
            embed = embeds.info_embed(
                "Ini Soal Order yang Mana?",
                "Kamu punya lebih dari satu order aktif -- pilih yang bener "
                "biar staff tau ini soal order yang mana.",
            )
            await message.channel.send(
                embed=embed,
                view=PendingOrderSelectView(active, message.content, attachment_urls),
            )
            return

        order = active[0]
        sent = await order_actions.forward_to_staff(
            self.bot, order["id"], message.author, message.content, attachment_urls
        )

        if attachment_urls:
            if sent:
                await message.channel.send(
                    embed=embeds.success_embed(f"Udah dikirim ke staff buat Order #{order['id']}.")
                )
            else:
                await message.channel.send(
                    embed=embeds.error_embed(
                        "Staff belum atur channel order-log, jadi ini gak bisa diterusin "
                        "otomatis. Tunggu staff cek order kamu manual ya."
                    )
                )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PaymentProofCog(bot))
