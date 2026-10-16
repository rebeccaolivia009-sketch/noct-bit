"""
Sticky Message: pesan yang SELALU nempel di paling bawah channel.

Cara kerja: tiap ada pesan baru di channel yang punya sticky aktif, bot
ngirim ulang sticky-nya ke bawah, baru ngehapus yang lama (kirim dulu
baru hapus, jadi kalau ngirimnya gagal sticky lama tetep ada).

Biar aman buat channel ramai:
  * DEBOUNCE -- 10 pesan beruntun cuma ngejadwalin SATU repost. Repost jalan
    DELAY detik setelah pesan pertama, dan sticky yang dikirim itu otomatis
    ada di bawah semua pesan yang masuk sampai saat itu.
  * Minimal MIN_INTERVAL detik antar repost di channel yang sama.
  * Lock per channel, jadi repost + pasang dari panel gak bisa tabrakan.
  * Channel yang dipantau di-cache di memori -- listener on_message gak
    nyentuh database buat pesan di channel biasa.

Panel setting: /sticky set (lihat bot.ui.sticky).
"""

from __future__ import annotations

import asyncio
import time

import discord
from discord import app_commands
from discord.ext import commands

from bot.core.logger import logger
from bot.database.queries import stickies as stickies_q
from bot.ui import embeds
from bot.ui.sticky import StickyPanel, build_view
from bot.utils import sticky_spec as spec
from bot.utils.permissions import staff_only


class StickyCog(commands.Cog):
    """Sticky message + panel setting-nya."""

    DELAY = 3.0             # detik tunggu setelah pesan baru sebelum repost
    MIN_INTERVAL = 8.0      # jeda minimal antar repost di satu channel
    OWN_POST_GRACE = 5.0    # pesan bot sendiri sesaat setelah repost gak dianggep pemicu

    sticky_group = app_commands.Group(
        name="sticky",
        description="Kelola sticky message (pesan yang nempel di bawah channel).",
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._ids: dict[int, int | None] = {}   # channel_id -> message_id sticky terpasang (channel aktif aja)
        self._tasks: dict[int, asyncio.Task] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._last_post: dict[int, float] = {}

    async def cog_load(self) -> None:
        rows = await stickies_q.list_enabled(self.bot.db)
        self._ids = {
            int(r["channel_id"]): (int(r["message_id"]) if r["message_id"] else None) for r in rows
        }
        logger.info("Sticky: %d channel dipantau.", len(self._ids))

    async def cog_unload(self) -> None:
        for task in self._tasks.values():
            task.cancel()

    # -- listener ---------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        channel_id = message.channel.id
        if channel_id not in self._ids:
            return  # channel biasa -- nol kerja, nol query
        if message.id == self._ids.get(channel_id):
            return  # ini sticky-nya sendiri
        if (
            self.bot.user is not None
            and message.author.id == self.bot.user.id
            and time.monotonic() - self._last_post.get(channel_id, float("-inf")) < self.OWN_POST_GRACE
        ):
            return  # event pesan sticky yang barusan dikirim (bisa nyampe sebelum id-nya kecatat)
        task = self._tasks.get(channel_id)
        if task is not None and not task.done():
            return  # udah ada repost terjadwal -- pesan ini ikut ke-cover
        self._tasks[channel_id] = asyncio.create_task(self._repost_later(channel_id))

    async def _repost_later(self, channel_id: int) -> None:
        try:
            elapsed = time.monotonic() - self._last_post.get(channel_id, float("-inf"))
            await asyncio.sleep(max(self.DELAY, self.MIN_INTERVAL - elapsed))
            if channel_id not in self._ids:
                return
            ok, error = await self._post(channel_id)
            if not ok:
                logger.warning("Sticky: gagal repost di channel %s: %s", channel_id, error)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Sticky: error pas repost di channel %s.", channel_id)

    # -- pasang / copot ----------------------------------------------------------

    async def _channel(self, channel_id: int):
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException:
                return None
        return channel

    @staticmethod
    async def _delete_message(channel, message_id: int | None) -> None:
        if not message_id:
            return
        try:
            await channel.get_partial_message(message_id).delete()
        except discord.HTTPException:
            pass  # udah kehapus / gak ada izin -- gapapa

    def _cancel_pending(self, channel_id: int) -> None:
        task = self._tasks.pop(channel_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def _post(self, channel_id: int) -> tuple[bool, str | None]:
        """Kirim sticky terbaru ke bawah channel, lalu hapus yang lama."""
        lock = self._locks.setdefault(channel_id, asyncio.Lock())
        async with lock:
            row = await stickies_q.get_by_channel(self.bot.db, channel_id)
            if row is None or not row["enabled"]:
                return True, None
            channel = await self._channel(channel_id)
            if channel is None:
                return False, "Channel gak ketemu."
            old_id = row["message_id"]
            self._last_post[channel_id] = time.monotonic()
            try:
                message = await channel.send(
                    view=build_view(spec.values_from_row(row)),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except discord.Forbidden:
                return False, "Bot gak punya izin kirim pesan di channel itu."
            except discord.HTTPException as exc:
                return False, f"Discord error: {exc}"
            await stickies_q.set_message_id(self.bot.db, channel_id, message.id)
            self._ids[channel_id] = message.id
            await self._delete_message(channel, old_id)
            return True, None

    async def apply(self, channel_id: int) -> tuple[bool, str | None]:
        """Sinkronin channel ini dengan data tersimpan: aktif -> pasang
        (kirim ulang ke bawah), nonaktif/hilang -> copot pesannya. Dipanggil
        panel pas Simpan."""
        self._cancel_pending(channel_id)
        row = await stickies_q.get_by_channel(self.bot.db, channel_id)
        if row is None or not row["enabled"]:
            self._ids.pop(channel_id, None)
            if row is not None and row["message_id"]:
                channel = await self._channel(channel_id)
                if channel is not None:
                    await self._delete_message(channel, row["message_id"])
                await stickies_q.set_message_id(self.bot.db, channel_id, None)
            return True, None
        self._ids[channel_id] = int(row["message_id"]) if row["message_id"] else None
        return await self._post(channel_id)

    async def remove(self, channel_id: int) -> bool:
        """Copot pesannya dan hapus pengaturannya. False kalau emang gak ada."""
        self._cancel_pending(channel_id)
        row = await stickies_q.get_by_channel(self.bot.db, channel_id)
        self._ids.pop(channel_id, None)
        if row is None:
            return False
        if row["message_id"]:
            channel = await self._channel(channel_id)
            if channel is not None:
                await self._delete_message(channel, row["message_id"])
        await stickies_q.delete(self.bot.db, channel_id)
        return True

    # -- command ------------------------------------------------------------------

    @sticky_group.command(name="set", description="Buka panel buat bikin/edit sticky message di sebuah channel.")
    @app_commands.describe(channel="Channel tempat sticky message dipasang")
    @staff_only()
    async def set_sticky(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        row = await stickies_q.get_by_channel(self.bot.db, channel.id)
        panel = StickyPanel(interaction.user.id, channel, row)
        await interaction.response.send_message(view=await panel.render(self.bot), ephemeral=True)

    @sticky_group.command(name="list", description="Liat semua sticky message di server ini.")
    @staff_only()
    async def list_stickies(self, interaction: discord.Interaction) -> None:
        rows = await stickies_q.list_guild(self.bot.db, interaction.guild.id)
        if not rows:
            await interaction.response.send_message(
                embed=embeds.info_embed("Sticky Message", "Belum ada. Bikin lewat `/sticky set`."), ephemeral=True
            )
            return
        lines = []
        for r in rows:
            if not r["enabled"]:
                state = "\u23F8\uFE0F nonaktif"
            elif r["message_id"]:
                state = "\U0001F4CC terpasang"
            else:
                state = "\u26A0\uFE0F belum terpasang"
            lines.append(f"<#{r['channel_id']}> \u2014 **{r['title']}** \u2014 {state}")
        await interaction.response.send_message(
            embed=embeds.info_embed("Sticky Message", "\n".join(lines)), ephemeral=True
        )

    @sticky_group.command(name="repost", description="Pasang ulang sticky ke paling bawah sekarang juga.")
    @app_commands.describe(channel="Channel yang sticky-nya mau dipasang ulang")
    @staff_only()
    async def repost(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        row = await stickies_q.get_by_channel(self.bot.db, channel.id)
        if row is None:
            await interaction.response.send_message(
                embed=embeds.error_embed("Channel itu belum punya sticky. Bikin lewat `/sticky set`."), ephemeral=True
            )
            return
        if not row["enabled"]:
            await interaction.response.send_message(
                embed=embeds.error_embed("Sticky di channel itu lagi nonaktif. Aktifin dulu lewat `/sticky set`."),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        ok, error = await self.apply(channel.id)
        embed = embeds.success_embed(f"Sticky dipasang ulang di {channel.mention}.") if ok else embeds.error_embed(error or "Gagal.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @sticky_group.command(name="remove", description="Hapus sticky message di sebuah channel.")
    @app_commands.describe(channel="Channel yang sticky-nya mau dihapus")
    @staff_only()
    async def remove_sticky(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        removed = await self.remove(channel.id)
        embed = (
            embeds.success_embed(f"Sticky di {channel.mention} dihapus.")
            if removed else embeds.error_embed("Channel itu gak punya sticky.")
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StickyCog(bot))
