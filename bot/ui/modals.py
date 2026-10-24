"""
Komponen Modal buat NOCTRA.

Modal dipake khusus di tempat yang inputnya cuma ketauan pas runtime
(dynamic checkout field yang diatur admin) atau butuh satu jawaban teks
pendek bebas (alasan close/cancel/refund). Semua yang bentuknya udah pasti
dan jelas tipenya (kategori, category type, produk, metode pembayaran)
ditangani lewat opsi slash command aja, yang emang pola discord.py yang
lebih pas buat CRUD terstruktur dan bikin autocomplete tetep jalan di
command itu.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord

from bot.core.logger import logger
from bot.database.queries.fields import MODAL_BATCH_SIZE
from bot.ui import embeds

MULTILINE_TYPES = {"login", "custom"}


def _style_for(field_row) -> discord.TextStyle:
    if field_row["field_type"] in MULTILINE_TYPES and (field_row["max_length"] or 0) > 100:
        return discord.TextStyle.paragraph
    return discord.TextStyle.short


class DynamicFieldsModal(discord.ui.Modal):
    """Satu batch (maks 5) checkout field yang diatur admin."""

    def __init__(
        self,
        *,
        title: str,
        fields_batch: list,
        on_submit_callback: Callable[[discord.Interaction, dict], Awaitable[None]],
    ) -> None:
        super().__init__(title=title[:45])
        self.fields_batch = fields_batch
        self._on_submit_callback = on_submit_callback
        self._inputs: dict[int, discord.ui.TextInput] = {}

        for field_row in fields_batch:
            required = bool(field_row["required"])
            text_input = discord.ui.TextInput(
                label=field_row["label"][:45],
                placeholder=(field_row["placeholder"] or "")[:100] or None,
                required=required,
                # Field wajib dikunci minimal 1 karakter juga di sisi Discord --
                # sebagian klien (terutama mobile) bisa ngirim field wajib
                # kosong kalau cuma ngandelin `required`.
                min_length=max(1 if required else 0, field_row["min_length"] or 0),
                max_length=min(max(field_row["max_length"] or 100, 1), 4000),
                style=_style_for(field_row),
            )
            self.add_item(text_input)
            self._inputs[field_row["id"]] = text_input

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values = {field_id: ti.value for field_id, ti in self._inputs.items()}
        await self._on_submit_callback(interaction, values)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception("Error di modal Info Checkout.", exc_info=error)
        try:
            embed = embeds.error_embed("Ada yang error pas ngirim form. Coba lagi ya.")
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            pass


class _OpenNextBatchButton(discord.ui.Button):
    def __init__(self, label: str, opener: Callable[[discord.Interaction], Awaitable[None]]) -> None:
        super().__init__(label=label, emoji="\U0001F4DD", style=discord.ButtonStyle.success)
        self._opener = opener

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._opener(interaction)


async def _delete_quietly(messages: list[discord.Message]) -> None:
    for message in messages:
        try:
            await message.delete()
        except discord.HTTPException:
            pass


async def collect_dynamic_fields(
    interaction: discord.Interaction,
    fields: list,
    on_complete: Callable[[discord.Interaction, dict], Awaitable[None]],
) -> None:
    """
    Ngumpulin value `fields` lewat modal (maks 5 input per modal -- batas
    Discord, jadi form yang lebih panjang dipecah beberapa bagian).

    `on_complete(interaction, {field_id: value})` HANYA dipanggil setelah
    SEMUA bagian disubmit -- gak ada langkah selanjutnya (pembayaran, dst)
    yang jalan sebelum itu. Kalau customer nutup/batalin modal, gak ada
    yang terjadi: prompt yang ngebuka modal tetep ada, tinggal diklik lagi.

    Discord GAK ngebolehin sebuah modal dibales modal lain, jadi antar
    bagian ada pesan dengan tombol "Lanjut isi data" (klik tombol boleh
    ngebuka modal). Pesan-pesan perantara itu dihapus begitu form lengkap.
    """
    batches = [
        fields[i : i + MODAL_BATCH_SIZE] for i in range(0, len(fields), MODAL_BATCH_SIZE)
    ]
    collected: dict[int, str] = {}
    prompts: list[discord.Message] = []

    def modal_for(index: int) -> DynamicFieldsModal:
        title = "Info Checkout" if len(batches) == 1 else f"Info Checkout ({index + 1}/{len(batches)})"
        return DynamicFieldsModal(
            title=title,
            fields_batch=batches[index],
            on_submit_callback=lambda i, v: handle_batch(index, i, v),
        )

    async def handle_batch(index: int, inter: discord.Interaction, values: dict) -> None:
        collected.update(values)
        next_index = index + 1
        if next_index < len(batches):
            async def open_next(next_inter: discord.Interaction) -> None:
                await next_inter.response.send_modal(modal_for(next_index))

            view = discord.ui.View(timeout=600)
            view.add_item(_OpenNextBatchButton(f"Lanjut isi data ({next_index + 1}/{len(batches)})", open_next))
            await inter.response.send_message(
                embed=embeds.info_embed(
                    "Data Tersimpan Sementara",
                    f"Bagian {index + 1} dari {len(batches)} udah masuk. Klik tombol di bawah buat ngisi bagian berikutnya.",
                ),
                view=view,
            )
            try:
                prompts.append(await inter.original_response())
            except discord.HTTPException:
                pass
            return
        await _delete_quietly(prompts)
        await on_complete(inter, collected)

    await interaction.response.send_modal(modal_for(0))


class ReviewTextModal(discord.ui.Modal):
    """Langkah terakhir alur review button-only -- rating bintangnya udah
    dipilih lewat tombol sebelum ini kebuka, jadi ini cuma nanyain bagian
    teks opsionalnya."""

    review_text = discord.ui.TextInput(
        label="Tulis review (opsional)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500,
        placeholder="Ceritain pengalaman kamu...",
    )

    def __init__(
        self,
        title: str,
        on_submit_callback: Callable[[discord.Interaction, str], Awaitable[None]],
    ) -> None:
        super().__init__(title=title[:45])
        self._on_submit_callback = on_submit_callback

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit_callback(interaction, str(self.review_text.value or "").strip())


class ReasonModal(discord.ui.Modal):
    """Modal satu field yang bisa dipake ulang buat alasan close/cancel/refund."""

    reason = discord.ui.TextInput(
        label="Alasan",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=300,
        placeholder="Opsional -- jelasin kenapa",
    )

    def __init__(
        self,
        title: str,
        on_submit_callback: Callable[[discord.Interaction, str], Awaitable[None]],
    ) -> None:
        super().__init__(title=title[:45])
        self._on_submit_callback = on_submit_callback

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit_callback(interaction, str(self.reason.value or "").strip())


class MessageModal(discord.ui.Modal):
    """Modal satu field wajib yang bisa dipake ulang -- dipake buat tombol
    'Balas' di order-log biar staff bisa DM customer tanpa perlu ngetik
    /order message."""

    message = discord.ui.TextInput(
        label="Pesan ke customer",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
        placeholder="Ketik balesan kamu di sini...",
    )

    def __init__(
        self,
        title: str,
        on_submit_callback: Callable[[discord.Interaction, str], Awaitable[None]],
    ) -> None:
        super().__init__(title=title[:45])
        self._on_submit_callback = on_submit_callback

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit_callback(interaction, str(self.message.value).strip())


class QuantityModal(discord.ui.Modal):
    """Satu field angka buat ngetik jumlah langsung (kalau mau beli
    banyak, lebih cepet daripada neken tombol tambah berkali-kali). Dipake
    di detail produk dan di tiap baris keranjang. Angka yang kegedean
    otomatis dijepit ke `max_quantity` (ngetik 500 pas sisa 12 jadi 12)."""

    def __init__(
        self,
        title: str,
        current: int,
        max_quantity: int,
        on_submit_callback: Callable[[discord.Interaction, int], Awaitable[None]],
    ) -> None:
        super().__init__(title=title[:45])
        self._max = max_quantity
        self._on_submit_callback = on_submit_callback
        self.amount = discord.ui.TextInput(
            label=f"Jumlah (1-{max_quantity})"[:45],
            style=discord.TextStyle.short,
            required=True,
            min_length=1,
            max_length=4,
            placeholder="Contoh: 5",
            default=str(current),
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.amount.value).strip()
        if not raw.isdigit() or int(raw) < 1:
            await interaction.response.send_message(
                embed=embeds.error_embed("Jumlah harus angka bulat minimal 1."), ephemeral=True
            )
            return
        await self._on_submit_callback(interaction, min(int(raw), self._max))
