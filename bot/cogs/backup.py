"""
Command admin: /backup -- export/import SELURUH data bot NOCTRA dalam satu
file.

Databasenya SQLite, yang emang udah satu file utuh dari sononya (biasanya
`data/noctra.db`). Jadi "backup" di sini bukan bikin format export
custom -- cukup ambil file itu apa adanya buat /backup export, dan timpa
balik file itu apa adanya buat /backup import. Ini yang bikin pindah
hosting gampang: download sekali lewat /backup export, upload sekali lewat
/backup import di hosting baru, kelar -- semua kategori/produk/order/review/
setting ikut pindah persis.
"""

from __future__ import annotations

import os
import shutil

import discord
from discord import app_commands
from discord.ext import commands

from bot.core.logger import logger
from bot.ui import embeds
from bot.utils.permissions import admin_only, staff_only

SQLITE_MAGIC_HEADER = b"SQLite format 3\x00"
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # batas upload lampiran Discord standar


class BackupImportConfirmView(discord.ui.View):
    """Konfirmasi sekali lagi sebelum nimpa SELURUH database -- ini aksi
    yang gak bisa di-undo lewat bot (walau ada auto-backup file lama
    sebagai jaring pengaman, lihat _do_import)."""

    def __init__(self, attachment: discord.Attachment) -> None:
        super().__init__(timeout=120)
        self.attachment = attachment

    @discord.ui.button(label="Ya, Timpa Semua Data", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _do_import(interaction, self.attachment)
        self.stop()

    @discord.ui.button(label="Batal", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=embeds.info_embed("Dibatalin", "Import gak jadi dilakuin -- data yang ada sekarang gak diapa-apain."),
            view=None,
        )
        self.stop()


async def _do_import(interaction: discord.Interaction, attachment: discord.Attachment) -> None:
    bot = interaction.client
    db = bot.db  # type: ignore[attr-defined]

    data = await attachment.read()
    if not data.startswith(SQLITE_MAGIC_HEADER):
        await interaction.followup.send(
            embed=embeds.error_embed("File ini bukan file database SQLite yang valid -- import dibatalin, data lama aman."),
            ephemeral=True,
        )
        return

    old_path = db.path
    safety_backup_path = f"{old_path}.before-import"

    try:
        # Tutup koneksi dulu sebelum nyentuh file-nya langsung.
        await db.close()

        # Jaring pengaman: simpen salinan file yang lagi aktif sebelum
        # ditimpa, jadi kalau file yang diimpor ternyata salah/corrupt,
        # masih bisa balik manual.
        if os.path.exists(old_path):
            shutil.copy2(old_path, safety_backup_path)

        # File WAL/SHM lama gak relevan lagi buat database yang mau
        # digantiin total -- hapus biar gak ketinggalan data basi yang
        # nyampur sama database baru.
        for ext in ("-wal", "-shm"):
            stale = old_path + ext
            if os.path.exists(stale):
                os.remove(stale)

        with open(old_path, "wb") as f:
            f.write(data)

        # Sambung ulang + jalanin schema/migration -- jaga-jaga kalau file
        # yang diimpor itu dari versi bot yang lebih lama dan belum punya
        # kolom/tabel terbaru.
        await db.connect()
        await db.init_schema()
    except Exception as exc:  # noqa: BLE001
        logger.exception("Gagal impor backup database.")
        await interaction.followup.send(
            embed=embeds.error_embed(
                f"Gagal impor: {exc}\n\nFile sebelum import ke-backup di `{safety_backup_path}` di server -- "
                "hubungin developer bot buat bantu balikin manual kalau perlu."
            ),
            ephemeral=True,
        )
        return

    await interaction.followup.send(
        embed=embeds.success_embed(
            "Data berhasil diimpor! Semua kategori/produk/order/review/setting sekarang sesuai file yang "
            f"kamu upload. File sebelum import ke-backup di `{safety_backup_path}` di server kalau-kalau perlu di-roll-back."
        ),
        ephemeral=True,
    )


class BackupCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    backup_group = app_commands.Group(
        name="backup", description="Export/import seluruh data bot buat pindah hosting.", guild_only=True
    )

    @backup_group.command(name="export", description="Download seluruh data bot (satu file) -- buat pindah hosting.")
    @staff_only()
    async def export(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        db = self.bot.db  # type: ignore[attr-defined]

        # Paksa semua perubahan yang masih nangkring di WAL file kegabung
        # ke file utama dulu, biar file yang diexport bener-bener lengkap
        # (SQLite WAL mode nyimpen perubahan terbaru di file -wal terpisah
        # sampe di-checkpoint).
        try:
            await db.conn.execute("PRAGMA wal_checkpoint(FULL);")
            await db.conn.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Gagal checkpoint WAL sebelum export -- lanjut aja, harusnya tetep aman.")

        path = db.path
        if not os.path.exists(path):
            await interaction.followup.send(embed=embeds.error_embed("File database gak ketemu di server."), ephemeral=True)
            return

        size = os.path.getsize(path)
        if size > MAX_UPLOAD_BYTES:
            await interaction.followup.send(
                embed=embeds.error_embed(
                    f"Database-nya udah {size / 1024 / 1024:.1f}MB, kelewat batas upload Discord (25MB). "
                    "Hubungin developer bot buat cara backup manual."
                ),
                ephemeral=True,
            )
            return

        file = discord.File(path, filename="noctra-backup.db")
        await interaction.followup.send(
            embed=embeds.success_embed(
                "Ini backup lengkap data bot kamu (kategori, produk, order, review, setting, semuanya). "
                "Simpen file ini baik-baik -- pas mau pindah hosting, tinggal upload file ini lewat "
                "`/backup import` di server yang baru."
            ),
            file=file,
            ephemeral=True,
        )

    @backup_group.command(name="import", description="Timpa SEMUA data bot pake file backup (.db) -- PERMANEN, hati-hati.")
    @app_commands.describe(file="File backup .db yang mau diimpor (hasil dari /backup export)")
    @admin_only()
    async def import_backup(self, interaction: discord.Interaction, file: discord.Attachment) -> None:
        if not file.filename.endswith(".db"):
            await interaction.response.send_message(
                embed=embeds.error_embed("File harus berformat `.db` (hasil dari `/backup export`)."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=embeds.error_embed(
                "⚠️ Ini bakal **nimpa SEMUA data yang ada sekarang** (kategori, produk, order, review, "
                "setting) pake isi file yang kamu upload. Gak bisa di-undo lewat bot (walau file lama "
                "otomatis ke-backup di server). Yakin mau lanjut?"
            ),
            view=BackupImportConfirmView(file),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BackupCog(bot))
