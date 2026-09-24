"""
Command staff: /roblox -- kelola katalog item Roblox limited. SATU panel
bisa nampung BANYAK ITEM BEDA-BEDA (beda gambar, beda stock/harga, beda
link) -- staff geser Sebelumnya/Selanjutnya buat pindah antar item, gak
perlu bikin panel terpisah tiap item. Lihat bot.ui.views.
RobloxCatalogView & RobloxSlideButton buat panelnya.

Alur pemakaian:
  1. /roblox catalog create [panel_title] -- bikin katalog (panel) baru,
     balesannya ngasih tau ID katalognya.
  2. /roblox item add catalog_id:<> item_title:<> stock_info:<>
     gambar:<upload> [link_label] [link_url] -- ulang buat tiap item
     yang mau ditambahin ke katalog itu.
  3. /roblox panel catalog_id:<> -- posting panelnya di channel ini.

Item yang udah ada bisa diedit/dihapus belakangan (/roblox item edit,
/roblox item remove) -- perubahannya kepake begitu ada yang geser slide
ke item itu lagi (container di-rebuild dari data DB terbaru tiap
diklik), atau kalau staff posting ulang lewat /roblox panel.
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import roblox_listings as roblox_q
from bot.ui import embeds
from bot.ui.views import RobloxCatalogView
from bot.utils.permissions import staff_only


class RobloxListingCog(commands.Cog):
    """Bikin & kelola katalog item Roblox limited + panel slideshow-nya."""

    roblox_group = app_commands.Group(
        name="roblox", description="Kelola katalog item Roblox limited (slideshow antar item + link beli).", guild_only=True
    )
    catalog_group = app_commands.Group(name="catalog", description="Kelola katalog (panel).", parent=roblox_group)
    item_group = app_commands.Group(name="item", description="Kelola item di dalam katalog.", parent=roblox_group)

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- /roblox catalog create/list/delete --------------------------------------

    @catalog_group.command(name="create", description="Bikin katalog (panel) item Roblox baru.")
    @app_commands.describe(panel_title="Judul panel -- misal 'KATALOG ITEM LIMITED'")
    @staff_only()
    async def catalog_create(
        self, interaction: discord.Interaction, panel_title: str = "\U0001F3AF KATALOG ITEM LIMITED"
    ) -> None:
        catalog_id = await roblox_q.create_catalog(self.bot.db, interaction.guild_id, panel_title)
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Katalog dibuat (ID: `{catalog_id}`).\n\n"
                f"Tambahin item lewat `/roblox item add catalog_id:{catalog_id}`, terus posting "
                f"panelnya lewat `/roblox panel catalog_id:{catalog_id}`."
            ),
            ephemeral=True,
        )

    @catalog_group.command(name="list", description="Liat semua katalog item Roblox server ini.")
    @staff_only()
    async def catalog_list(self, interaction: discord.Interaction) -> None:
        rows = await roblox_q.list_catalogs(self.bot.db, interaction.guild_id)
        if not rows:
            await interaction.response.send_message(
                embed=embeds.info_embed("Katalog Roblox", "Belum ada katalog."), ephemeral=True
            )
            return
        lines = []
        for row in rows:
            count = len(await roblox_q.list_items(self.bot.db, row["id"]))
            lines.append(f"`{row['id']}` -- **{row['panel_title']}** ({count} item)")
        await interaction.response.send_message(
            embed=embeds.info_embed("Katalog Roblox", "\n".join(lines)), ephemeral=True
        )

    @catalog_group.command(name="delete", description="Hapus katalog item Roblox (item-itemnya ikut kehapus).")
    @app_commands.describe(catalog_id="ID katalog yang mau dihapus")
    @staff_only()
    async def catalog_delete(self, interaction: discord.Interaction, catalog_id: int) -> None:
        catalog = await roblox_q.get_catalog(self.bot.db, catalog_id)
        if catalog is None:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Katalog ID `{catalog_id}` gak ketemu."), ephemeral=True
            )
            return
        await roblox_q.delete_catalog(self.bot.db, catalog_id)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Katalog **{catalog['panel_title']}** dihapus."), ephemeral=True
        )

    # -- /roblox item add/edit/remove --------------------------------------------

    @item_group.command(name="add", description="Tambahin satu item ke katalog.")
    @app_commands.describe(
        catalog_id="ID katalog (liat di /roblox catalog list)",
        item_title="Nama item -- misal 'Dominus Empyreus'",
        stock_info="Info stock/harga, bebas format -- misal 'Stock: 2 | Harga: Rp150.000'",
        gambar="Gambar item ini",
        link_label="Teks tombol link",
        link_url="URL tombol link -- misal link trade/checkout/katalog Roblox",
    )
    @staff_only()
    async def item_add(
        self,
        interaction: discord.Interaction,
        catalog_id: int,
        item_title: str,
        stock_info: str,
        gambar: discord.Attachment,
        link_label: str = "Beli Sekarang",
        link_url: str = "https://www.roblox.com/",
    ) -> None:
        if not gambar.content_type or not gambar.content_type.startswith("image/"):
            await interaction.response.send_message(
                embed=embeds.error_embed("File yang diupload harus berupa gambar."), ephemeral=True
            )
            return
        catalog = await roblox_q.get_catalog(self.bot.db, catalog_id)
        if catalog is None:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Katalog ID `{catalog_id}` gak ketemu."), ephemeral=True
            )
            return

        await roblox_q.add_item(self.bot.db, catalog_id, item_title, gambar.url, stock_info, link_label, link_url)
        total = len(await roblox_q.list_items(self.bot.db, catalog_id))
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Item **{item_title}** ditambahin ke **{catalog['panel_title']}** (sekarang total {total} item)."
            ),
            ephemeral=True,
        )

    @item_group.command(name="edit", description="Edit satu item (kosongin parameter yang gak mau diubah).")
    @app_commands.describe(
        catalog_id="ID katalog",
        nomor="Nomor urut item (liat di /roblox item list)",
        item_title="Nama item baru (kosongin kalau gak diubah)",
        stock_info="Info stock baru (kosongin kalau gak diubah)",
        link_label="Teks tombol link baru (kosongin kalau gak diubah)",
        link_url="URL tombol link baru (kosongin kalau gak diubah)",
    )
    @staff_only()
    async def item_edit(
        self,
        interaction: discord.Interaction,
        catalog_id: int,
        nomor: int,
        item_title: str | None = None,
        stock_info: str | None = None,
        link_label: str | None = None,
        link_url: str | None = None,
    ) -> None:
        ok = await roblox_q.update_item_at(
            self.bot.db, catalog_id, nomor - 1,
            item_title=item_title, stock_info=stock_info, link_label=link_label, link_url=link_url,
        )
        if not ok:
            await interaction.response.send_message(
                embed=embeds.error_embed("Nomor item gak valid -- cek lagi urutannya di `/roblox item list`."),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=embeds.success_embed("Item diupdate."), ephemeral=True)

    @item_group.command(name="remove", description="Hapus satu item dari katalog.")
    @app_commands.describe(catalog_id="ID katalog", nomor="Nomor urut item yang mau dihapus (liat di /roblox item list)")
    @staff_only()
    async def item_remove(self, interaction: discord.Interaction, catalog_id: int, nomor: int) -> None:
        removed = await roblox_q.remove_item_at(self.bot.db, catalog_id, nomor - 1)
        if not removed:
            await interaction.response.send_message(
                embed=embeds.error_embed("Nomor item gak valid -- cek lagi urutannya di `/roblox item list`."),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=embeds.success_embed("Item dihapus."), ephemeral=True)

    @item_group.command(name="list", description="Liat semua item di satu katalog.")
    @app_commands.describe(catalog_id="ID katalog")
    @staff_only()
    async def item_list(self, interaction: discord.Interaction, catalog_id: int) -> None:
        items = await roblox_q.list_items(self.bot.db, catalog_id)
        if not items:
            await interaction.response.send_message(
                embed=embeds.info_embed("Item Katalog", "Belum ada item di katalog ini."), ephemeral=True
            )
            return
        lines = [f"`{i + 1}.` **{item['item_title']}** -- {item['stock_info']}" for i, item in enumerate(items)]
        await interaction.response.send_message(
            embed=embeds.info_embed("Item Katalog", "\n".join(lines)), ephemeral=True
        )

    # -- /roblox panel ------------------------------------------------------------

    @roblox_group.command(name="panel", description="Posting panel katalog ini di channel ini.")
    @app_commands.describe(catalog_id="ID katalog (liat di /roblox catalog list)")
    @staff_only()
    async def panel(self, interaction: discord.Interaction, catalog_id: int) -> None:
        catalog = await roblox_q.get_catalog(self.bot.db, catalog_id)
        if catalog is None:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Katalog ID `{catalog_id}` gak ketemu."), ephemeral=True
            )
            return
        items = await roblox_q.list_items(self.bot.db, catalog_id)
        if not items:
            await interaction.response.send_message(
                embed=embeds.error_embed(
                    "Katalog ini belum ada item sama sekali -- tambahin dulu lewat `/roblox item add`."
                ),
                ephemeral=True,
            )
            return

        await roblox_q.set_current_index(self.bot.db, catalog_id, 0)
        view = RobloxCatalogView(catalog, items, 0)
        msg = await interaction.channel.send(view=view)
        await roblox_q.set_message_ref(self.bot.db, catalog_id, interaction.channel_id, msg.id)
        await interaction.response.send_message(embed=embeds.success_embed("Panel katalog udah diposting."), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RobloxListingCog(bot))
