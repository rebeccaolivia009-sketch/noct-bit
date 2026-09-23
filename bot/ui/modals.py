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

from bot.database.queries.fields import MODAL_BATCH_SIZE

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
            text_input = discord.ui.TextInput(
                label=field_row["label"][:45],
                placeholder=(field_row["placeholder"] or "")[:100] or None,
                required=bool(field_row["required"]),
                min_length=max(0, field_row["min_length"] or 0),
                max_length=min(max(field_row["max_length"] or 100, 1), 4000),
                style=_style_for(field_row),
            )
            self.add_item(text_input)
            self._inputs[field_row["id"]] = text_input

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values = {field_id: ti.value for field_id, ti in self._inputs.items()}
        await self._on_submit_callback(interaction, values)


async def collect_dynamic_fields(
    interaction: discord.Interaction,
    fields: list,
    on_complete: Callable[[discord.Interaction, dict], Awaitable[None]],
) -> None:
    """
    Mulai (bisa berantai) modal buat ngumpulin value `fields`.

    `on_complete(interaction, {field_id: value})` di-await begitu semua
    batch udah disubmit. Discord modal maksimal 5 text input, jadi field
    dipecah jadi beberapa batch dan dirantai: tiap submit modal buka modal
    berikutnya sebagai respon *awal*-nya (Discord ngewajibin ini -- gak bisa
    defer terus baru buka modal belakangan).
    """
    batches = [
        fields[i : i + MODAL_BATCH_SIZE] for i in range(0, len(fields), MODAL_BATCH_SIZE)
    ]
    collected: dict[int, str] = {}

    async def handle_batch(batch_index: int, inter: discord.Interaction, values: dict) -> None:
        collected.update(values)
        next_index = batch_index + 1
        if next_index < len(batches):
            modal = DynamicFieldsModal(
                title=f"Info Checkout ({next_index + 1}/{len(batches)})",
                fields_batch=batches[next_index],
                on_submit_callback=lambda i, v: handle_batch(next_index, i, v),
            )
            await inter.response.send_modal(modal)
        else:
            await on_complete(inter, collected)

    first_modal = DynamicFieldsModal(
        title=f"Info Checkout (1/{len(batches)})" if len(batches) > 1 else "Info Checkout",
        fields_batch=batches[0],
        on_submit_callback=lambda i, v: handle_batch(0, i, v),
    )
    await interaction.response.send_modal(first_modal)


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
