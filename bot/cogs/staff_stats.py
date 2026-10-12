"""
Command staff: /staffstats -- performa staff.

  /staffstats [periode]              -- peringkat semua staff (maks 10).
  /staffstats staff:<> [periode]     -- detail satu staff.

Yang dihitung: order diselesaikan, pembayaran diverifikasi, batal/refund,
median waktu proses (lunas -> selesai), ticket di-claim, median waktu respon
pertama ticket, dan bukti pengiriman. Median dipake (bukan rata-rata) biar
satu order yang nyangkut lama gak ngerusak angka.

Event order & respon ticket baru kecatat SEJAK fitur ini dipasang;
claim ticket dan bukti pengiriman diambil dari data lama.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.ui import components
from bot.utils import staff_stats as stats_util
from bot.utils.permissions import staff_only


class StaffStatsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="staffstats", description="Statistik performa staff (order, respon ticket, pengiriman).")
    @app_commands.describe(staff="Kosongkan untuk peringkat semua staff", periode="Rentang waktu (default: 7 hari)")
    @app_commands.choices(
        periode=[app_commands.Choice(name=label, value=key) for key, label in stats_util.PERIODS.items()]
    )
    @app_commands.guild_only()
    @staff_only()
    async def staffstats(
        self, interaction: discord.Interaction, staff: discord.Member | None = None,
        periode: app_commands.Choice[str] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        key = periode.value if periode else "7d"
        label = stats_util.PERIODS[key]
        stats = await stats_util.gather(self.bot.db, stats_util.period_since(key))

        if staff is not None:
            card = {
                "mode": "detail", "period": label,
                "staff": {
                    "id": staff.id, "name": staff.name, "display_name": staff.display_name,
                    "avatar_url": staff.display_avatar.url,
                },
                "stats": stats.get(staff.id) or stats_util._blank(),
            }
        else:
            rows = stats_util.ranked_rows(stats)
            card = {
                "mode": "overview", "period": label, "rows": rows,
                "totals": {
                    "completed": sum(v["completed"] for v in stats.values()),
                    "paid": sum(v["paid"] for v in stats.values()),
                },
            }
        await interaction.followup.send(
            view=components.NoctraLayout(components.staff_stats_container(card), timeout=None),
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StaffStatsCog(bot))
