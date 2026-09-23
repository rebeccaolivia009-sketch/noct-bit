"""Global error handling buat slash command.

Disentralisasi di sini biar gak ada try/except berulang di tiap cog dan
mastiin user selalu dapet embed yang rapi daripada gagal diem-diem atau
raw traceback, sementara detail lengkapnya tetep kelog di server."""

from __future__ import annotations

import discord
from discord import app_commands

from bot.core.logger import logger
from bot.ui.embeds import error_embed


def setup_error_handler(bot) -> None:
    tree = bot.tree

    async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CheckFailure):
            message = str(error) or "Kamu gak punya izin buat pake command ini."
        elif isinstance(error, app_commands.CommandOnCooldown):
            message = f"Command ini lagi cooldown. Coba lagi dalam {error.retry_after:.1f} detik ya."
        elif isinstance(error, app_commands.TransformerError):
            message = "Salah satu value yang kamu masukin gak valid."
        else:
            logger.exception("Unhandled app command error", exc_info=error)
            message = "Ada yang error pas jalanin command itu. Staff udah dikasih tau."

        embed = error_embed(message)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            logger.exception("Gagal ngirim pesan error ke user.")

    tree.on_error = on_error
