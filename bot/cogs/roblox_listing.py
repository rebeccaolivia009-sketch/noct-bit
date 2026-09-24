"""
Command staff: /roblox -- kelola listing item Roblox limited (panel
slideshow gambar + info stock + tombol link beli/trade). Lihat
bot.ui.views.RobloxListingView & RobloxSlideButton buat panelnya.

Alur pemakaian:
  1. /roblox create judul:<> info_stock:<> gambar:<upload> [link_label]
     [link_url] -- bikin listing baru, gambar PERTAMA wajib diupload
     langsung di sini. Balesannya ngasih tau ID listing-nya.
  2. /roblox image add id:<> gambar:<upload> -- ulang sesuka hati kalau
     mau nambah gambar lain buat slideshow-nya.
  3. /roblox panel id:<> -- posting panelnya di channel ini.

Listing yang udah keposting bisa diedit belakangan (/roblox edit,
/roblox image add/remove) -- tapi perubahannya CUMA kepake di panel yang
lagi aktif begitu ada yang klik Sebelumnya/Selanjutnya (container-nya
di-rebuild dari data DB terbaru tiap diklik), atau kalau staff posting
ulang lewat /roblox panel. Gak auto-refresh sendiri kayak leaderboard --
listing item biasanya gak seresponsif itu perlunya.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import roblox_listings as roblox_q
from bot.ui import embeds
from bot.ui.views import RobloxListingView
from bot.utils.permissions import staff_only


class RobloxListingCog(commands.Cog):
    """Bikin & kelola listing item Roblox limited + panel slideshow-nya."""

    listing_group = app_commands.Group(
        name="roblox", description="Kelola listing item Roblox limited (slideshow gambar + link beli).", guild_only=True
    )
    image_group = app_commands.Group(
        name="image", description="Kelola gambar slideshow listing.", parent=listing_group
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- /roblox create -----------------------------------------------------

    @listing_group.command(name="create", description="Bikin listing item Roblox baru.")
    @app_commands.describe(
        judul="Nama item -- misal 'Dominus Empyreus'",
        info_stock="Info stock/harga, bebas format -- misal 'Stock: 2 | Harga: Rp150.000'",
        gambar="Gambar pertama item ini (bisa nambah lagi lewat /roblox image add)",
        link_label="Teks tombol link",
        link_url="URL tombol link -- misal link trade/checkout/katalog Roblox",
    )
    @staff_only()
    async def create(
        self,
        interaction: discord.Interaction,
        judul: str,
        info_stock: str,
        gambar: discord.Attachment,
        link_label: str = "Beli Sekarang",
        link_url: str = "https://www.roblox.com/",
    ) -> None:
        if not gambar.content_type or not gambar.content_type.startswith("image/"):
            await interaction.response.send_message(
                embed=embeds.error_embed("File yang diupload harus berupa gambar."), ephemeral=True
            )
            return

        listing_id = await roblox_q.create_listing(
            self.bot.db, interaction.guild_id, judul, info_stock, link_label, link_url
        )
        await roblox_q.add_image(self.bot.db, listing_id, gambar.url)

        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Listing **{judul}** dibuat (ID: `{listing_id}`).\n\n"
                f"Tambahin gambar lain lewat `/roblox image add id:{listing_id}`, terus posting "
                f"panelnya lewat `/roblox panel id:{listing_id}`."
            ),
            ephemeral=True,
        )

    # -- /roblox image add/remove --------------------------------------------

    @image_group.command(name="add", description="Tambahin satu gambar ke slideshow listing.")
    @app_commands.describe(id="ID listing (liat di /roblox list)", gambar="Gambar yang mau ditambahin")
    @staff_only()
    async def image_add(self, interaction: discord.Interaction, id: int, gambar: discord.Attachment) -> None:
        if not gambar.content_type or not gambar.content_type.startswith("image/"):
            await interaction.response.send_message(
                embed=embeds.error_embed("File yang diupload harus berupa gambar."), ephemeral=True
            )
            return
        listing = await roblox_q.get_listing(self.bot.db, id)
        if listing is None:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Listing ID `{id}` gak ketemu."), ephemeral=True
            )
            return

        await roblox_q.add_image(self.bot.db, id, gambar.url)
        total = len(await roblox_q.list_images(self.bot.db, id))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Gambar ditambahin ke **{listing['title']}** (sekarang total {total} gambar)."),
            ephemeral=True,
        )

    @image_group.command(name="remove", description="Hapus salah satu gambar dari slideshow listing.")
    @app_commands.describe(id="ID listing", nomor="Nomor urut gambar yang mau dihapus (liat di /roblox list)")
    @staff_only()
    async def image_remove(self, interaction: discord.Interaction, id: int, nomor: int) -> None:
        listing = await roblox_q.get_listing(self.bot.db, id)
        if listing is None:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Listing ID `{id}` gak ketemu."), ephemeral=True
            )
            return
        removed = await roblox_q.remove_image_at(self.bot.db, id, nomor - 1)
        if not removed:
            await interaction.response.send_message(
                embed=embeds.error_embed("Nomor gambar gak valid -- cek lagi urutannya di `/roblox list`."),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=embeds.success_embed("Gambar dihapus."), ephemeral=True)

    # -- /roblox edit ---------------------------------------------------------

    @listing_group.command(name="edit", description="Edit info listing (kosongin parameter yang gak mau diubah).")
    @app_commands.describe(
        id="ID listing",
        judul="Judul baru (kosongin kalau gak diubah)",
        info_stock="Info stock baru (kosongin kalau gak diubah)",
        link_label="Teks tombol link baru (kosongin kalau gak diubah)",
        link_url="URL tombol link baru (kosongin kalau gak diubah)",
    )
    @staff_only()
    async def edit(
        self,
        interaction: discord.Interaction,
        id: int,
        judul: str | None = None,
        info_stock: str | None = None,
        link_label: str | None = None,
        link_url: str | None = None,
    ) -> None:
        listing = await roblox_q.get_listing(self.bot.db, id)
        if listing is None:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Listing ID `{id}` gak ketemu."), ephemeral=True
            )
            return
        await roblox_q.update_listing(
            self.bot.db, id, title=judul, stock_info=info_stock, link_label=link_label, link_url=link_url
        )
        await interaction.response.send_message(embed=embeds.success_embed("Listing diupdate."), ephemeral=True)

    # -- /roblox panel ----------------------------------------------------------

    @listing_group.command(name="panel", description="Posting panel slideshow listing ini di channel ini.")
    @app_commands.describe(id="ID listing (liat di /roblox list)")
    @staff_only()
    async def panel(self, interaction: discord.Interaction, id: int) -> None:
        listing = await roblox_q.get_listing(self.bot.db, id)
        if listing is None:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Listing ID `{id}` gak ketemu."), ephemeral=True
            )
            return
        images = await roblox_q.list_images(self.bot.db, id)
        if not images:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Listing ini belum ada gambar sama sekali -- tambahin dulu lewat `/roblox image add`."
                ),
                ephemeral=True,
            )
            return

        await roblox_q.set_current_index(self.bot.db, id, 0)
        view = RobloxListingView(listing, images, 0)
        msg = await interaction.channel.send(view=view)
        await roblox_q.set_message_ref(self.bot.db, id, interaction.channel_id, msg.id)
        await interaction.response.send_message(embed=embeds.success_embed("Panel listing udah diposting."), ephemeral=True)

    # -- /roblox list / delete ----------------------------------------------------

    @listing_group.command(name="list", description="Liat semua listing item Roblox server ini.")
    @staff_only()
    async def list_listings(self, interaction: discord.Interaction) -> None:
        rows = await roblox_q.list_listings(self.bot.db, interaction.guild_id)
        if not rows:
            await interaction.response.send_message(
                embed=embeds.info_embed("Listing Roblox", "Belum ada listing."), ephemeral=True
            )
            return
        lines = []
        for row in rows:
            count = len(await roblox_q.list_images(self.bot.db, row["id"]))
            lines.append(f"`{row['id']}` -- **{row['title']}** ({count} gambar)")
        await interaction.response.send_message(
            embed=embeds.info_embed("Listing Roblox", "\n".join(lines)), ephemeral=True
        )

    @listing_group.command(name="delete", description="Hapus listing item Roblox (gambar-gambarnya ikut kehapus).")
    @app_commands.describe(id="ID listing yang mau dihapus")
    @staff_only()
    async def delete(self, interaction: discord.Interaction, id: int) -> None:
        listing = await roblox_q.get_listing(self.bot.db, id)
        if listing is None:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Listing ID `{id}` gak ketemu."), ephemeral=True
            )
            return
        await roblox_q.delete_listing(self.bot.db, id)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Listing **{listing['title']}** dihapus."), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RobloxListingCog(bot))
