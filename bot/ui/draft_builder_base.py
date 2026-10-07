"""
Base class buat panel kontrol builder pesan Components V2 -- dipake bareng
sama PanelBuilderView (/panel) dan AnnouncementBuilderView (/announcement).

PENTING soal cara update pesan panel: pesan panel ini ephemeral, dan pesan
ephemeral GAK BISA di-edit lewat `message.edit()` biasa kalau dipanggil
dari interaction yang beda sama yang lagi pegang pesan itu -- gagalnya
diem-diem (silent fail). Satu-satunya cara yang bener adalah manggil
`interaction.response.edit_message(...)` LANGSUNG dari interaction yang
lagi aktif saat itu (baik klik tombol langsung, atau submit modal yang
dibuka dari tombol di pesan yang sama -- Discord/discord.py ngedukung dua-
duanya buat ngedit balik pesan asalnya). Makanya semua method di sini
nerima `interaction` yang lagi aktif, BUKAN objek Message yang ditangkep
sebelumnya.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord

from bot.database.queries import panel_buttons as panel_buttons_q
from bot.ui import embeds
from bot.utils.message_draft import (
    BANNER_COUNT,
    BannerSpec,
    ButtonSpec,
    MessageDraft,
    SeparatorBlock,
    TextBlock,
    banner_slot_choices,
    effective_banner_position,
)
from bot.utils.validators import is_valid_emoji

MAX_UNDO_HISTORY = 20
MAX_BUTTONS = 5


class _SingleFieldModal(discord.ui.Modal):
    """Modal satu TextInput -- dipake bareng buat Title/Description/Add
    Line/Thumbnail/Banner/Color biar gak nulis 6 class modal yang isinya
    sama persis."""

    def __init__(
        self,
        title: str,
        label: str,
        *,
        style: discord.TextStyle = discord.TextStyle.short,
        max_length: int = 256,
        default: str | None = None,
        placeholder: str | None = None,
        required: bool = True,
        on_submit_callback: Callable[[discord.Interaction, str], Awaitable[None]],
    ) -> None:
        super().__init__(title=title[:45])
        self.value_input = discord.ui.TextInput(
            label=label[:45],
            style=style,
            max_length=max_length,
            default=default,
            placeholder=placeholder,
            required=required,
        )
        self.add_item(self.value_input)
        self._on_submit_callback = on_submit_callback

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit_callback(interaction, str(self.value_input.value or "").strip())


class _TwoFieldModal(discord.ui.Modal):
    """Modal dua TextInput -- dipake buat Add Link, yang nyisipin link
    markdown sebagai baris teks biasa (beda sama Add Link Button, yang
    bikin tombol Discord asli lewat _ThreeFieldModal di bawah)."""

    def __init__(
        self,
        title: str,
        label1: str,
        label2: str,
        *,
        max1: int = 100,
        max2: int = 500,
        on_submit_callback: Callable[[discord.Interaction, str, str], Awaitable[None]],
    ) -> None:
        super().__init__(title=title[:45])
        self.field1 = discord.ui.TextInput(label=label1[:45], max_length=max1)
        self.field2 = discord.ui.TextInput(label=label2[:45], max_length=max2, placeholder="https://...")
        self.add_item(self.field1)
        self.add_item(self.field2)
        self._on_submit_callback = on_submit_callback

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit_callback(
            interaction, str(self.field1.value).strip(), str(self.field2.value).strip()
        )


class _ThreeFieldModal(discord.ui.Modal):
    """Modal tiga TextInput -- dipake buat Add Link Button dan Add Reply
    Button (label + emoji opsional + url/isi balasan).

    `max2` (field emoji) defaultnya 100 -- SEBELUMNYA 20, yang kepotong
    buat emoji custom server kayak <a:nama_panjang:1234567890123456789>
    (gampang 35-45 karakter), jadi emoji custom gak pernah bisa keisi
    penuh biarpun formatnya valid. Emoji unicode biasa (1-4 karakter)
    tetep muat jauh di bawah batas baru ini."""

    def __init__(
        self,
        title: str,
        label1: str,
        label2: str,
        label3: str,
        *,
        max1: int = 80,
        max2: int = 100,
        max3: int = 500,
        style3: discord.TextStyle = discord.TextStyle.short,
        placeholder3: str | None = None,
        on_submit_callback: Callable[[discord.Interaction, str, str, str], Awaitable[None]],
    ) -> None:
        super().__init__(title=title[:45])
        self.field1 = discord.ui.TextInput(label=label1[:45], max_length=max1)
        self.field2 = discord.ui.TextInput(
            label=label2[:45], max_length=max2, required=False, placeholder="Kosongin kalau gak perlu"
        )
        self.field3 = discord.ui.TextInput(
            label=label3[:45], max_length=max3, style=style3, placeholder=placeholder3
        )
        self.add_item(self.field1)
        self.add_item(self.field2)
        self.add_item(self.field3)
        self._on_submit_callback = on_submit_callback

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit_callback(
            interaction,
            str(self.field1.value).strip(),
            str(self.field2.value).strip(),
            str(self.field3.value).strip(),
        )


class _ReplyButtonModal(discord.ui.Modal):
    """5 TextInput -- label, emoji (opsional), isi balasan, URL banner
    (opsional), URL thumbnail (opsional). Dipake Add Reply Button DAN
    Edit Reply Button, beda cuma di nilai default tiap field (Add =
    semua kosong, Edit = diisi dari row DB yang ada). 5 field itu PAS
    batas maksimal Discord buat satu modal, gak bisa nambah lagi."""

    def __init__(
        self,
        title: str,
        *,
        default_label: str = "",
        default_emoji: str = "",
        default_reply_text: str = "",
        default_image_url: str = "",
        default_thumbnail_url: str = "",
        on_submit_callback: Callable[[discord.Interaction, str, str, str, str, str], Awaitable[None]],
    ) -> None:
        super().__init__(title=title[:45])
        self.label_input = discord.ui.TextInput(
            label="Label tombol", max_length=80, default=default_label or None,
        )
        self.emoji_input = discord.ui.TextInput(
            label="Emoji (opsional)", max_length=100, required=False,
            default=default_emoji or None, placeholder="Kosongin kalau gak perlu",
        )
        self.reply_text_input = discord.ui.TextInput(
            label="Pesan yang muncul pas diklik", style=discord.TextStyle.paragraph, max_length=1000,
            default=default_reply_text or None,
        )
        self.image_input = discord.ui.TextInput(
            label="URL banner (opsional)", required=False, max_length=500,
            default=default_image_url or None, placeholder="https://...",
        )
        self.thumbnail_input = discord.ui.TextInput(
            label="URL thumbnail (opsional)", required=False, max_length=500,
            default=default_thumbnail_url or None, placeholder="https://...",
        )
        for item in (
            self.label_input, self.emoji_input, self.reply_text_input, self.image_input, self.thumbnail_input,
        ):
            self.add_item(item)
        self._on_submit_callback = on_submit_callback

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit_callback(
            interaction,
            str(self.label_input.value).strip(),
            str(self.emoji_input.value or "").strip(),
            str(self.reply_text_input.value).strip(),
            str(self.image_input.value or "").strip(),
            str(self.thumbnail_input.value or "").strip(),
        )


class ManageButtonsView(discord.ui.View):
    """Ngambil alih PESAN PANEL YANG SAMA sementara (bukan buka pesan
    ephemeral baru) -- biar hapus/edit tombol tetep bisa lewat
    interaction.response.edit_message() yang valid, terus balik lagi ke
    tampilan builder normal abis selesai. Milih satu tombol di sini CUMA
    nentuin tombol mana -- keputusan Edit atau Hapus-nya ada di
    ButtonActionView abis milih (lihat di bawah)."""

    def __init__(self, builder: "BaseDraftBuilderView") -> None:
        super().__init__(timeout=300)
        self.builder = builder
        options = [
            discord.SelectOption(
                label=b.label[:100],
                description=(b.url[:100] if b.is_link else "Tombol balasan pesan"),
                value=str(i),
            )
            for i, b in enumerate(builder.draft.buttons)
        ]
        select = discord.ui.Select(placeholder="Pilih tombol buat diedit/dihapus...", options=options, row=0)
        select.callback = self._on_select
        self.add_item(select)

        back_button = discord.ui.Button(label="Kembali", style=discord.ButtonStyle.secondary, row=1)
        back_button.callback = self._on_back
        self.add_item(back_button)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        select = self.children[0]
        idx = int(select.values[0])  # type: ignore[attr-defined]
        await interaction.response.edit_message(view=ButtonActionView(self.builder, idx))

    async def _on_back(self, interaction: discord.Interaction) -> None:
        await self.builder._after_edit(interaction)


class ButtonActionView(discord.ui.View):
    """Muncul abis staff milih SATU tombol di ManageButtonsView -- nawarin
    Edit atau Hapus buat tombol itu doang. Edit tombol LINK pake
    _ThreeFieldModal yang sama kayak Add Link Button (field-nya di-prefill
    nilai sekarang); edit tombol BALASAN pake _ReplyButtonModal (5 field,
    termasuk banner & thumbnail) dan nge-UPDATE row panel_reply_buttons
    yang udah ada (bukan bikin row baru), jadi button_id-nya (makanya
    custom_id tombol di pesan) tetep sama."""

    def __init__(self, builder: "BaseDraftBuilderView", index: int) -> None:
        super().__init__(timeout=300)
        self.builder = builder
        self.index = index

        edit_btn = discord.ui.Button(label="Edit", style=discord.ButtonStyle.primary, row=0)
        edit_btn.callback = self._on_edit
        self.add_item(edit_btn)

        delete_btn = discord.ui.Button(label="Hapus", style=discord.ButtonStyle.danger, row=0)
        delete_btn.callback = self._on_delete
        self.add_item(delete_btn)

        back_btn = discord.ui.Button(label="Kembali ke Daftar", style=discord.ButtonStyle.secondary, row=0)
        back_btn.callback = self._on_back
        self.add_item(back_btn)

    async def _on_delete(self, interaction: discord.Interaction) -> None:
        self.builder._snapshot()
        self.builder.draft.buttons.pop(self.index)
        await self.builder._after_edit(interaction)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(view=ManageButtonsView(self.builder))

    async def _on_edit(self, interaction: discord.Interaction) -> None:
        spec = self.builder.draft.buttons[self.index]

        if spec.is_link:
            async def on_submit(inter: discord.Interaction, label: str, emoji: str, url: str) -> None:
                if not url.startswith(("http://", "https://")):
                    await inter.response.send_message(
                        embed=embeds.error_embed("URL harus mulai dari http:// atau https://"), ephemeral=True
                    )
                    return
                if emoji and not is_valid_emoji(emoji):
                    await inter.response.send_message(embed=embeds.error_embed("Emoji-nya gak valid."), ephemeral=True)
                    return
                self.builder._snapshot()
                self.builder.draft.buttons[self.index] = ButtonSpec(
                    label=label or "Klik di sini", emoji=emoji or None, url=url
                )
                await self.builder._after_edit(inter)
                await inter.followup.send(embed=embeds.success_embed("Tombol link diupdate."), ephemeral=True)

            modal = _ThreeFieldModal(
                "Edit Tombol Link", "Label tombol", "Emoji (opsional)", "URL",
                placeholder3="https://...", on_submit_callback=on_submit,
            )
            modal.field1.default = spec.label
            modal.field2.default = spec.emoji or None
            modal.field3.default = spec.url or None
            await interaction.response.send_modal(modal)
            return

        # Tombol BALASAN -- tarik dulu reply_text/banner/thumbnail yang
        # sekarang dari DB (gak kesimpen di ButtonSpec/draft, cuma label +
        # emoji + button_id yang ada di situ) buat ngisi default modal.
        db = interaction.client.db  # type: ignore[attr-defined]
        row = await panel_buttons_q.get_reply_button(db, spec.reply_button_id)
        current_reply_text = row["reply_text"] if row else ""
        current_image = row["image_url"] if row and "image_url" in row.keys() and row["image_url"] else ""
        current_thumb = row["thumbnail_url"] if row and "thumbnail_url" in row.keys() and row["thumbnail_url"] else ""

        async def on_submit(
            inter: discord.Interaction, label: str, emoji: str, reply_text: str, image_url: str, thumbnail_url: str
        ) -> None:
            if not reply_text:
                await inter.response.send_message(embed=embeds.error_embed("Isi balasannya gak boleh kosong."), ephemeral=True)
                return
            if emoji and not is_valid_emoji(emoji):
                await inter.response.send_message(embed=embeds.error_embed("Emoji-nya gak valid."), ephemeral=True)
                return
            label = label or "Klik di sini"
            await panel_buttons_q.update_reply_button(
                inter.client.db,  # type: ignore[attr-defined]
                spec.reply_button_id,
                label=label, reply_text=reply_text,
                image_url=image_url or None, thumbnail_url=thumbnail_url or None,
            )
            self.builder._snapshot()
            self.builder.draft.buttons[self.index] = ButtonSpec(
                label=label, emoji=emoji or None, reply_button_id=spec.reply_button_id
            )
            await self.builder._after_edit(inter)
            await inter.followup.send(embed=embeds.success_embed("Tombol balasan diupdate."), ephemeral=True)

        modal = _ReplyButtonModal(
            "Edit Tombol Balasan",
            default_label=spec.label, default_emoji=spec.emoji or "",
            default_reply_text=current_reply_text, default_image_url=current_image,
            default_thumbnail_url=current_thumb,
            on_submit_callback=on_submit,
        )
        await interaction.response.send_modal(modal)


class ManageLinesView(discord.ui.View):
    """Sama pola kayak ManageButtonsView -- ngambil alih pesan panel yang
    sama sementara, biar staff bisa pilih SATU baris teks (TextBlock,
    SeparatorBlock gak ikut kehitung) buat diedit atau dihapus, terus
    balik ke tampilan builder normal abis selesai."""

    def __init__(self, builder: "BaseDraftBuilderView") -> None:
        super().__init__(timeout=300)
        self.builder = builder
        # Simpen index ASLI di draft.blocks (bukan cuma nomor urutan baris
        # teks) -- soalnya ada SeparatorBlock yang kesisip di antaranya,
        # jadi "baris ke-3" belum tentu draft.blocks[2].
        self.text_block_indices = [
            i for i, b in enumerate(builder.draft.blocks) if isinstance(b, TextBlock)
        ]
        options = [
            discord.SelectOption(
                label=f"Baris {n + 1}: {builder.draft.blocks[i].content[:80]}",
                value=str(i),
            )
            for n, i in enumerate(self.text_block_indices)
        ]
        select = discord.ui.Select(placeholder="Pilih baris buat diedit/dihapus...", options=options[:25], row=0)
        select.callback = self._on_select
        self.add_item(select)

        back_button = discord.ui.Button(label="Kembali", style=discord.ButtonStyle.secondary, row=1)
        back_button.callback = self._on_back
        self.add_item(back_button)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        select = [c for c in self.children if isinstance(c, discord.ui.Select)][0]
        block_index = int(select.values[0])  # type: ignore[attr-defined]
        current_block = self.builder.draft.blocks[block_index]

        async def on_submit(inter: discord.Interaction, value: str) -> None:
            self.builder._snapshot()
            if value:
                self.builder.draft.blocks[block_index] = TextBlock(value)
            else:
                self.builder.draft.blocks.pop(block_index)
            self.builder._build_separator_select()
            await self.builder._after_edit(inter)
            await inter.followup.send(
                embed=embeds.success_embed("Baris diupdate." if value else "Baris dihapus."), ephemeral=True
            )

        modal = _SingleFieldModal(
            "Edit Baris", "Teks (kosongin buat hapus baris ini)", style=discord.TextStyle.paragraph,
            max_length=1000, default=current_block.content, required=False, on_submit_callback=on_submit,
        )
        await interaction.response.send_modal(modal)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        await self.builder._after_edit(interaction)


class ManageBannersView(discord.ui.View):
    """Sub-panel buat ngatur DUA banner -- ngambil alih pesan panel yang
    sama sementara (pola sama kayak ManageLinesView), tapi beda: tiap
    perubahan di sini langsung kepush live (lewat builder._push_live) dan
    panelnya TETEP kebuka, jadi staff bisa ngatur URL / on-off / posisi
    berkali-kali tanpa buka ulang. Klik "Kembali" buat balik ke builder.

    Baris 0: pilih banner mana yang lagi diatur (tombol nunjukin status
    On/Off/Kosong). Baris 1: Set URL, On/Off, Hapus, Kembali. Baris 2:
    Select posisi banner yang lagi dipilih."""

    def __init__(self, builder: "BaseDraftBuilderView", selected: int = 0) -> None:
        super().__init__(timeout=300)
        self.builder = builder
        self.selected = selected
        self._rebuild()

    @property
    def spec(self) -> BannerSpec:
        return self.builder.draft.banners[self.selected]

    def _rebuild(self) -> None:
        """Bangun ulang semua komponen dari state draft sekarang -- dipanggil
        tiap ada perubahan biar label/status/opsi Select selalu sinkron."""
        self.clear_items()
        draft = self.builder.draft

        for index, spec in enumerate(draft.banners):
            if not spec.url:
                state = "Kosong"
            else:
                state = "On" if spec.enabled else "Off"
            button = discord.ui.Button(
                label=f"Banner {index + 1}: {state}",
                style=discord.ButtonStyle.primary if index == self.selected else discord.ButtonStyle.secondary,
                row=0,
            )
            button.callback = self._make_pick_callback(index)
            self.add_item(button)

        spec = self.spec
        has_url = bool(spec.url)

        set_url_button = discord.ui.Button(
            label="Ganti URL" if has_url else "Set URL", style=discord.ButtonStyle.secondary, row=1
        )
        set_url_button.callback = self._on_set_url
        self.add_item(set_url_button)

        toggle_button = discord.ui.Button(
            label="Aktif: On" if spec.enabled else "Aktif: Off",
            style=discord.ButtonStyle.success if spec.enabled else discord.ButtonStyle.secondary,
            row=1,
            disabled=not has_url,
        )
        toggle_button.callback = self._on_toggle
        self.add_item(toggle_button)

        remove_button = discord.ui.Button(label="Hapus", style=discord.ButtonStyle.danger, row=1, disabled=not has_url)
        remove_button.callback = self._on_remove
        self.add_item(remove_button)

        back_button = discord.ui.Button(label="Kembali", style=discord.ButtonStyle.secondary, row=1)
        back_button.callback = self._on_back
        self.add_item(back_button)

        current = effective_banner_position(draft, spec)
        options = [
            discord.SelectOption(label=label[:100], value=value, default=(value == current))
            for value, label in banner_slot_choices(draft)
        ]
        select = discord.ui.Select(
            placeholder=f"Posisi Banner {self.selected + 1}...", options=options, row=2
        )
        select.callback = self._on_position
        self.add_item(select)

    async def _apply(self, interaction: discord.Interaction) -> None:
        """Refresh panel ini + push perubahan ke pesan target. WAJIB jadi
        response PERTAMA `interaction` (edit_message), sama alasannya kayak
        BaseDraftBuilderView._after_edit."""
        self.builder._sync_toggle_labels()
        self._rebuild()
        await interaction.response.edit_message(view=self, **self.builder._preview_kwargs())
        await self.builder._push_live(interaction)

    def _make_pick_callback(self, index: int):
        async def callback(interaction: discord.Interaction) -> None:
            self.selected = index
            self._rebuild()
            await interaction.response.edit_message(view=self)

        return callback

    async def _on_set_url(self, interaction: discord.Interaction) -> None:
        index = self.selected

        async def on_submit(inter: discord.Interaction, value: str) -> None:
            if value and not value.lower().startswith(("http://", "https://")):
                await inter.response.send_message(
                    embed=embeds.error_embed("URL banner harus diawali `http://` atau `https://`."), ephemeral=True
                )
                return
            self.builder._snapshot()
            spec = self.builder.draft.banners[index]
            spec.url = value or None
            if value:
                spec.enabled = True  # ngasih URL baru = langsung dinyalain
            await self._apply(inter)

        modal = _SingleFieldModal(
            f"Atur Banner {index + 1}", "URL gambar banner (kosongin buat hapus)", default=self.spec.url,
            required=False, placeholder="https://...", on_submit_callback=on_submit,
        )
        await interaction.response.send_modal(modal)

    async def _on_toggle(self, interaction: discord.Interaction) -> None:
        self.builder._snapshot()
        self.spec.enabled = not self.spec.enabled
        await self._apply(interaction)

    async def _on_remove(self, interaction: discord.Interaction) -> None:
        self.builder._snapshot()
        self.spec.url = None
        self.spec.enabled = True
        await self._apply(interaction)

    async def _on_position(self, interaction: discord.Interaction) -> None:
        select = [c for c in self.children if isinstance(c, discord.ui.Select)][0]
        self.builder._snapshot()
        self.spec.position = select.values[0]  # type: ignore[attr-defined]
        await self._apply(interaction)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        self.builder._sync_toggle_labels()
        await self.builder._after_edit(interaction)


class BaseDraftBuilderView(discord.ui.View):
    def __init__(self, *, timeout: float | None = 1800) -> None:
        super().__init__(timeout=timeout)
        self.draft = MessageDraft()
        self.undo_stack: list[MessageDraft] = []
        self._build_separator_select()

    # -- Helper -----------------------------------------------------------

    def _snapshot(self) -> None:
        self.undo_stack.append(self.draft.copy())
        if len(self.undo_stack) > MAX_UNDO_HISTORY:
            self.undo_stack.pop(0)

    def _build_separator_select(self) -> None:
        for item in list(self.children):
            if isinstance(item, discord.ui.Select):
                self.remove_item(item)
        options = []
        line_no = 0
        for blk in self.draft.blocks:
            if isinstance(blk, TextBlock):
                line_no += 1
                options.append(discord.SelectOption(label=f"Sebelum baris ke-{line_no}", value=str(line_no - 1)))
        options.append(discord.SelectOption(label="Di paling akhir", value="end"))
        select = discord.ui.Select(placeholder="Sisipin garis pemisah...", options=options[:25], row=3)
        select.callback = self._on_separator_select
        self.add_item(select)
        # Method ini dipanggil tiap draft diganti total (init, Undo, Reset,
        # load draft lama) -- sekalian sinkronin label tombol toggle biar
        # selalu nunjukin state draft yang lagi aktif.
        self._sync_toggle_labels()

    def _sync_toggle_labels(self) -> None:
        """Samain label/warna tombol toggle tata letak dengan state draft."""
        draft = self.draft
        active_banners = sum(1 for b in draft.banners if b.url and b.enabled)
        self.banner_button.label = f"Banner ({active_banners}/{BANNER_COUNT})"
        self.thumbnail_position_button.label = (
            "Thumb: Deskripsi" if draft.thumbnail_position == "description" else "Thumb: Judul"
        )
        self.separator_toggle_button.label = (
            "Pemisah Judul: On" if draft.title_description_separator else "Pemisah Judul: Off"
        )
        self.separator_toggle_button.style = (
            discord.ButtonStyle.success if draft.title_description_separator else discord.ButtonStyle.secondary
        )

    async def _after_edit(self, interaction: discord.Interaction) -> None:
        """Dipanggil abis draft berubah, dari action manapun (klik tombol
        langsung ATAU submit modal yang dibuka dari tombol di pesan ini) --
        WAJIB jadi response PERTAMA `interaction` ini lewat
        `interaction.response.edit_message()`, soalnya itu satu-satunya
        cara yang valid buat ngedit balik pesan ephemeral yang jadi asal
        komponen/modal-nya. Subclass override buat nambahin live-push ke
        pesan lain (target asli / pesan yang udah terkirim)."""
        await interaction.response.edit_message(view=self)

    def _preview_kwargs(self) -> dict:
        """Kwargs tambahan buat `edit_message()` yang nampilin ulang preview
        draft di pesan panel (dipake ManageBannersView). Default kosong --
        AnnouncementBuilderView override buat ngasih embed preview-nya."""
        return {}

    async def _push_live(self, interaction: discord.Interaction) -> None:
        """Simpen/push draft sekarang ke tujuan aslinya (pesan target /panel,
        pesan pengumuman yang udah terkirim) TANPA ngubah pesan panel ini --
        dipanggil SETELAH response interaction udah dipakai. Default gak
        ngapa-ngapain; subclass yang override."""
        return None

    async def _on_separator_select(self, interaction: discord.Interaction) -> None:
        select = [c for c in self.children if isinstance(c, discord.ui.Select)][0]
        value = select.values[0]  # type: ignore[attr-defined]
        self._snapshot()
        if value == "end":
            self.draft.blocks.append(SeparatorBlock())
        else:
            idx_text = int(value)
            count = -1
            insert_at = len(self.draft.blocks)
            for i, blk in enumerate(self.draft.blocks):
                if isinstance(blk, TextBlock):
                    count += 1
                    if count == idx_text:
                        insert_at = i
                        break
            self.draft.blocks.insert(insert_at, SeparatorBlock())
        self._build_separator_select()
        await self._after_edit(interaction)

    # -- Title / Description / Add Line ------------------------------------

    @discord.ui.button(label="Title", style=discord.ButtonStyle.secondary, row=0)
    async def title_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def on_submit(inter: discord.Interaction, value: str) -> None:
            self._snapshot()
            self.draft.title = value or None
            await self._after_edit(inter)
            await inter.followup.send(
                embed=embeds.success_embed("Judul diatur." if value else "Judul dihapus."), ephemeral=True
            )

        modal = _SingleFieldModal(
            "Atur Judul", "Judul (kosongin buat hapus)", default=self.draft.title,
            required=False, on_submit_callback=on_submit,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Description", style=discord.ButtonStyle.secondary, row=0)
    async def description_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def on_submit(inter: discord.Interaction, value: str) -> None:
            self._snapshot()
            self.draft.description = value or None
            await self._after_edit(inter)
            await inter.followup.send(
                embed=embeds.success_embed("Deskripsi diatur." if value else "Deskripsi dihapus."), ephemeral=True
            )

        modal = _SingleFieldModal(
            "Atur Deskripsi", "Deskripsi (kosongin buat hapus)", style=discord.TextStyle.paragraph,
            max_length=2000, default=self.draft.description, required=False, on_submit_callback=on_submit,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Add Line", style=discord.ButtonStyle.secondary, row=0)
    async def add_line_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def on_submit(inter: discord.Interaction, value: str) -> None:
            if not value:
                await inter.response.send_message(embed=embeds.error_embed("Isinya gak boleh kosong."), ephemeral=True)
                return
            self._snapshot()
            self.draft.blocks.append(TextBlock(value))
            self._build_separator_select()
            await self._after_edit(inter)
            await inter.followup.send(embed=embeds.success_embed("Baris baru ditambahin."), ephemeral=True)

        modal = _SingleFieldModal(
            "Tambah Baris", "Teks", style=discord.TextStyle.paragraph, max_length=1000,
            on_submit_callback=on_submit,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Edit Line", style=discord.ButtonStyle.secondary, row=0)
    async def edit_line_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not any(isinstance(b, TextBlock) for b in self.draft.blocks):
            await interaction.response.send_message(
                embed=embeds.error_embed("Belum ada baris yang bisa diedit."), ephemeral=True
            )
            return
        await interaction.response.edit_message(view=ManageLinesView(self))

    # -- Thumbnail / Banner -------------------------------------------------

    @discord.ui.button(label="Thumbnail", style=discord.ButtonStyle.secondary, row=0)
    async def thumbnail_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def on_submit(inter: discord.Interaction, value: str) -> None:
            self._snapshot()
            self.draft.thumbnail_url = value or None
            await self._after_edit(inter)
            await inter.followup.send(
                embed=embeds.success_embed("Thumbnail diatur." if value else "Thumbnail dihapus."), ephemeral=True
            )

        modal = _SingleFieldModal(
            "Atur Thumbnail", "URL gambar (kosongin buat hapus)", default=self.draft.thumbnail_url,
            required=False, placeholder="https://...", on_submit_callback=on_submit,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Banner (0/2)", style=discord.ButtonStyle.secondary, row=1)
    async def banner_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # Buka sub-panel buat ngatur dua banner (URL, on/off, posisi).
        await interaction.response.edit_message(view=ManageBannersView(self))

    # -- Toggle tata letak (row 1, di sebelah Banner) ----------------------

    @discord.ui.button(label="Thumb: Deskripsi", style=discord.ButtonStyle.secondary, row=1)
    async def thumbnail_position_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._snapshot()
        self.draft.thumbnail_position = "title" if self.draft.thumbnail_position == "description" else "description"
        self._sync_toggle_labels()
        await self._after_edit(interaction)

    @discord.ui.button(label="Pemisah Judul: Off", style=discord.ButtonStyle.secondary, row=1)
    async def separator_toggle_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._snapshot()
        self.draft.title_description_separator = not self.draft.title_description_separator
        self._sync_toggle_labels()
        await self._after_edit(interaction)

    # -- Color / Undo / Reset / Add Link -------------------------------------

    @discord.ui.button(label="Color", style=discord.ButtonStyle.secondary, row=2)
    async def color_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def on_submit(inter: discord.Interaction, value: str) -> None:
            hex_value = value.strip().lstrip("#")
            try:
                color_int = int(hex_value, 16)
                if not (0 <= color_int <= 0xFFFFFF):
                    raise ValueError
            except ValueError:
                await inter.response.send_message(
                    embed=embeds.error_embed("Format warna gak valid. Pake kode hex, misal 7C5CFF."), ephemeral=True
                )
                return
            self._snapshot()
            self.draft.color = color_int
            await self._after_edit(inter)
            await inter.followup.send(embed=embeds.success_embed(f"Warna diatur ke #{hex_value.upper()}."), ephemeral=True)

        modal = _SingleFieldModal(
            "Atur Warna", "Kode warna hex (tanpa #)", max_length=6, placeholder="7C5CFF",
            default=f"{self.draft.color:06X}", on_submit_callback=on_submit,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Undo", style=discord.ButtonStyle.secondary, row=2)
    async def undo_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.undo_stack:
            await interaction.response.send_message(embed=embeds.error_embed("Gak ada history buat di-undo."), ephemeral=True)
            return
        self.draft = self.undo_stack.pop()
        self._build_separator_select()
        await self._after_edit(interaction)
        await interaction.followup.send(embed=embeds.success_embed("Draft di-undo ke versi sebelumnya."), ephemeral=True)

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.danger, row=2)
    async def reset_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self._snapshot()
        self.draft = MessageDraft()
        self._build_separator_select()
        await self._after_edit(interaction)
        await interaction.followup.send(
            embed=embeds.success_embed("Draft direset. Kepencet gak sengaja? Tinggal klik Undo."), ephemeral=True
        )

    @discord.ui.button(label="Add Link", style=discord.ButtonStyle.secondary, row=2)
    async def add_link_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def on_submit(inter: discord.Interaction, text: str, url: str) -> None:
            if not url.startswith(("http://", "https://")):
                await inter.response.send_message(
                    embed=embeds.error_embed("URL harus mulai dari http:// atau https://"), ephemeral=True
                )
                return
            self._snapshot()
            self.draft.blocks.append(TextBlock(f"[{text or url}]({url})"))
            self._build_separator_select()
            await self._after_edit(inter)
            await inter.followup.send(embed=embeds.success_embed("Link ditambahin sebagai baris teks."), ephemeral=True)

        modal = _TwoFieldModal("Tambah Link", "Teks yang ditampilin", "URL", on_submit_callback=on_submit)
        await interaction.response.send_modal(modal)

    # -- Add Link Button / Add Reply Button / Manage Buttons -----------------

    @discord.ui.button(label="Add Link Button", style=discord.ButtonStyle.secondary, row=4)
    async def add_link_button_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if len(self.draft.buttons) >= MAX_BUTTONS:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Maksimal {MAX_BUTTONS} tombol per pesan."), ephemeral=True
            )
            return

        async def on_submit(inter: discord.Interaction, label: str, emoji: str, url: str) -> None:
            if not url.startswith(("http://", "https://")):
                await inter.response.send_message(
                    embed=embeds.error_embed("URL harus mulai dari http:// atau https://"), ephemeral=True
                )
                return
            if emoji and not is_valid_emoji(emoji):
                await inter.response.send_message(embed=embeds.error_embed("Emoji-nya gak valid."), ephemeral=True)
                return
            self._snapshot()
            self.draft.buttons.append(ButtonSpec(label=label or "Klik di sini", emoji=emoji or None, url=url))
            await self._after_edit(inter)
            await inter.followup.send(embed=embeds.success_embed(f"Tombol link **{label}** ditambahin."), ephemeral=True)

        modal = _ThreeFieldModal(
            "Tambah Tombol Link", "Label tombol", "Emoji (opsional)", "URL",
            placeholder3="https://...", on_submit_callback=on_submit,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Add Reply Button", style=discord.ButtonStyle.secondary, row=4)
    async def add_reply_button_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if len(self.draft.buttons) >= MAX_BUTTONS:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"Maksimal {MAX_BUTTONS} tombol per pesan."), ephemeral=True
            )
            return

        async def on_submit(
            inter: discord.Interaction, label: str, emoji: str, reply_text: str, image_url: str, thumbnail_url: str
        ) -> None:
            if not reply_text:
                await inter.response.send_message(embed=embeds.error_embed("Isi balasannya gak boleh kosong."), ephemeral=True)
                return
            if emoji and not is_valid_emoji(emoji):
                await inter.response.send_message(embed=embeds.error_embed("Emoji-nya gak valid."), ephemeral=True)
                return
            db = inter.client.db  # type: ignore[attr-defined]
            label = label or "Klik di sini"
            button_id = await panel_buttons_q.create_reply_button(
                db, label, reply_text, image_url=image_url or None, thumbnail_url=thumbnail_url or None
            )
            self._snapshot()
            self.draft.buttons.append(ButtonSpec(label=label, emoji=emoji or None, reply_button_id=button_id))
            await self._after_edit(inter)
            await inter.followup.send(embed=embeds.success_embed(f"Tombol balasan **{label}** ditambahin."), ephemeral=True)

        modal = _ReplyButtonModal("Tambah Tombol Balasan", on_submit_callback=on_submit)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Manage Buttons", style=discord.ButtonStyle.secondary, row=4)
    async def manage_buttons_button(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not self.draft.buttons:
            await interaction.response.send_message(
                embed=embeds.info_embed("Kelola Tombol", "Belum ada tombol yang ditambahin."), ephemeral=True
            )
            return
        await interaction.response.edit_message(view=ManageButtonsView(self))
