"""
Dengerin customer yang ngirim foto buat dilampirin ke review mereka.

Discord Modal cuma dukung field input teks -- gak ada komponen yang nerima
upload file -- jadi masukin foto ke review gak bisa lewat modal
rating/teks itu sendiri. Sebagai gantinya, abis modal itu disubmit, NOCTRA
minta customer buat kirim aja gambarnya sebagai pesan DM biasa (gak perlu
link, gak perlu command), dan listener ini yang nangkep.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from bot.database.queries import reviews as reviews_q
from bot.ui import embeds
from bot.ui.views import build_join_server_view
from bot.utils import order_actions


class ReviewPhotoCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is not None:
            return  # cuma perhatiin DM dari user asli

        db = self.bot.db
        review = await reviews_q.get_awaiting_photo_review_for_user(db, message.author.id)
        if not review:
            return  # customer ini gak lagi diharapin ngirim foto review sekarang

        image_attachment = next(
            (a for a in message.attachments if (a.content_type or "").startswith("image/")), None
        )
        if not image_attachment:
            # Mereka lagi ngobrol soal hal lain (atau ngirim file bukan
            # gambar) -- jangan diem-diem dianggep "gak ada foto" dan
            # dilewatin; tunggu aja sampe ada gambar beneran atau tombol
            # Lewati diklik.
            return

        await reviews_q.update_review(db, review["id"], image_url=image_attachment.url)
        await reviews_q.set_awaiting_photo(db, review["id"], False)

        try:
            join_view = await build_join_server_view(db)
            await message.channel.send(
                embed=embeds.success_embed("Foto udah ditambahin ke review kamu. Makasih ya udah berbagi!"),
                view=join_view,
            )
        except discord.HTTPException:
            pass

        # Bersihin pesan prompt "Mau Tambahin Foto?" sekarang tugasnya udah kelar.
        await order_actions.cleanup_dm_messages(self.bot, review["order_id"])


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReviewPhotoCog(bot))
