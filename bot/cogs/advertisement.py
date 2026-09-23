"""
Command staff: /iklan -- posting embed iklan profesional ke channel
tujuan (biasanya channel promosi/partner). Dipisah dari /panel dan
/announcement (yang render Components V2 custom buat pesan bebas) karena
iklan cukup satu Embed klasik yang rapi: title, description, banner gede,
plus thumbnail kecil opsional -- gak butuh builder interaktif buat ini.

Gambar bisa dikasih dua cara:
  * Upload langsung (parameter `gambar`/`thumbnail`) -- direkomendasiin,
    file-nya ikut nempel ke pesan lewat attachment://... jadi gak
    tergantung hosting luar yang bisa mati atau ke-invalidate sewaktu-waktu.
  * URL (parameter `gambar_url`/`thumbnail_url`) -- dipake kalau gambarnya
    emang udah di-hosting di tempat lain (CDN, imgur, dst).

Kalau dua-duanya dikasih buat slot yang sama, upload attachment yang
menang -- URL-nya diabaikan diam-diam.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.core.theme import COLOR_ACCENT
from bot.ui import embeds
from bot.utils.helpers import RuntimeSettings
from bot.utils.permissions import staff_only
from bot.utils.validators import parse_hex_color

# Limit Discord buat embed title/description masing-masing 256 dan 4096
# karakter -- description dikasih sedikit ruang di bawah limit keras biar
# gak kepotong aneh kalau ada karakter multi-byte.
TITLE_MAX_LENGTH = 256
DESCRIPTION_MAX_LENGTH = 4000

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


async def _attachment_to_file(
    attachment: discord.Attachment, base_filename: str
) -> tuple[discord.File | None, str | None]:
    """Validasi attachment itu beneran gambar, lalu convert ke discord.File
    dengan nama file yang dipastiin unik (banner dan thumbnail dikasih
    nama beda) -- Discord nolak dua attachment dengan nama sama dalam satu
    pesan. Return (file, error_message)."""
    content_type = attachment.content_type or ""
    is_image_by_type = content_type.startswith("image/")
    is_image_by_ext = attachment.filename.lower().endswith(_IMAGE_EXTENSIONS)
    if not (is_image_by_type or is_image_by_ext):
        return None, f"`{attachment.filename}` kayaknya bukan file gambar (PNG/JPG/WebP/GIF)."

    ext = attachment.filename.rsplit(".", 1)[-1].lower() if "." in attachment.filename else "png"
    file = await attachment.to_file(filename=f"{base_filename}.{ext}")
    return file, None


class AdvertisementCog(commands.Cog):
    """Posting embed iklan yang rapi ke channel promosi."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="iklan", description="Posting embed iklan profesional ke channel tujuan.")
    @app_commands.describe(
        title="Judul iklan",
        description="Isi/deskripsi iklan",
        gambar="Upload gambar banner utama (PNG/JPG/WebP/GIF)",
        thumbnail="Upload gambar kecil buat thumbnail di pojok kanan atas (opsional)",
        gambar_url="URL gambar banner -- dipake kalau gak upload file (opsional)",
        thumbnail_url="URL gambar thumbnail -- dipake kalau gak upload file (opsional)",
        channel="Channel tujuan (default: channel iklan dari /settings ad_channel, atau channel ini)",
        warna="Warna aksen embed, kode hex misal #7C5CFF (opsional)",
    )
    @app_commands.guild_only()
    @staff_only()
    async def iklan(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        gambar: discord.Attachment | None = None,
        thumbnail: discord.Attachment | None = None,
        gambar_url: str | None = None,
        thumbnail_url: str | None = None,
        channel: discord.TextChannel | None = None,
        warna: str | None = None,
    ) -> None:
        if len(title) > TITLE_MAX_LENGTH:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Judul kepanjangan -- maksimal {TITLE_MAX_LENGTH} karakter."),
                ephemeral=True,
            )
            return
        if len(description) > DESCRIPTION_MAX_LENGTH:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Deskripsi kepanjangan -- maksimal {DESCRIPTION_MAX_LENGTH} karakter."),
                ephemeral=True,
            )
            return

        color, color_error = parse_hex_color(warna, COLOR_ACCENT)
        if color_error:
            await interaction.response.send_message(embed=embeds.error_embed(color_error), ephemeral=True)
            return

        # Tentuin channel tujuan: parameter eksplisit > default /settings
        # ad_channel > channel tempat command ini dijalanin.
        target_channel = channel
        if target_channel is None:
            default_channel_id = await RuntimeSettings(self.bot.db).ad_channel_id()
            if default_channel_id:
                maybe_channel = self.bot.get_channel(default_channel_id)
                if isinstance(maybe_channel, discord.TextChannel):
                    target_channel = maybe_channel
        if target_channel is None:
            if isinstance(interaction.channel, discord.TextChannel):
                target_channel = interaction.channel
            else:
                await interaction.response.send_message(
                    embed=embeds.error_embed(
                        "Gak bisa nentuin channel tujuan -- pilih lewat parameter `channel` atau atur "
                        "default lewat `/settings ad_channel` dulu."
                    ),
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True, thinking=True)

        embed = embeds.advertisement_embed(title, description, color=color)
        files: list[discord.File] = []

        if gambar is not None:
            file, error = await _attachment_to_file(gambar, "iklan_banner")
            if error:
                await interaction.followup.send(embed=embeds.error_embed(error), ephemeral=True)
                return
            files.append(file)
            embed.set_image(url=f"attachment://{file.filename}")
        elif gambar_url:
            embed.set_image(url=gambar_url)

        if thumbnail is not None:
            file, error = await _attachment_to_file(thumbnail, "iklan_thumb")
            if error:
                await interaction.followup.send(embed=embeds.error_embed(error), ephemeral=True)
                return
            files.append(file)
            embed.set_thumbnail(url=f"attachment://{file.filename}")
        elif thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        try:
            sent = await target_channel.send(embed=embed, files=files or None)
        except discord.HTTPException as exc:
            await interaction.followup.send(
                embed=embeds.error_embed(f"Gagal posting iklan ke {target_channel.mention}: {exc}"),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            embed=embeds.success_embed(
                f"Iklan udah diposting di {target_channel.mention}. [Lompat ke pesan]({sent.jump_url})"
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdvertisementCog(bot))
