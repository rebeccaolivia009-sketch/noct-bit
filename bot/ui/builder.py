"""
Panel Catalog Builder (/builder) -- satu panel buat ngatur SELURUH katalog:
Kategori, Category Type, Product Type, Produk, dan Field Checkout, tanpa
harus ngetik command satu-satu.

Cara kerjanya:
  * Satu pesan ephemeral (Components V2) yang di-edit terus. Navigasi
    drill-down: Home -> Kategori -> Category Type -> Product Type / Produk.
  * Tambah, edit, dan duplikat SELALU lewat DRAFT di memori: preview
    langsung keupdate tiap nilai berubah, dan daftar "Perubahan" nunjukin
    nilai lama -> baru. Database baru disentuh pas staff nekan Simpan.
  * Tombol preview customer nampilin tampilan asli yang dilihat pembeli.
  * Hapus selalu lewat layar konfirmasi yang nunjukin dampaknya, dan
    ditolak (dengan alasan + jalan keluar) kalau produknya punya riwayat
    order/review.

Aturan nilai/validasi ada di bot.utils.builder_spec, akses DB di
bot.utils.builder_store; file ini cuma tampilan dan alur interaksi.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import discord

from bot.core.logger import logger
from bot.core.theme import COLOR_ACCENT, COLOR_DANGER, COLOR_PRIMARY, COLOR_WARNING
from bot.database.queries import fields as fields_q
from bot.ui import components, embeds
from bot.utils import builder_spec as spec
from bot.utils import builder_store as store
from bot.utils.builder_spec import CATEGORY, FIELD, GROUP, KIND_LABEL, PRODUCT, TYPE
from bot.utils.validators import is_valid_emoji

PAGE_SIZE = 25
OWNER_ONLY = "Panel ini punya staff lain -- jalanin `/builder` buat buka panelmu sendiri."

_STYLE = discord.ButtonStyle


# ============================================================================
# KOMPONEN DASAR
# ============================================================================

class Btn(discord.ui.Button):
    def __init__(self, handler: Callable[[discord.Interaction], Awaitable[None]], **kwargs) -> None:
        super().__init__(**kwargs)
        self._handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._handler(interaction)


class Sel(discord.ui.Select):
    def __init__(self, handler: Callable[[discord.Interaction, str], Awaitable[None]], **kwargs) -> None:
        super().__init__(min_values=1, max_values=1, **kwargs)
        self._handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        await self._handler(interaction, self.values[0])


class FormModal(discord.ui.Modal):
    """Modal yang dibangun dari builder_spec.FORMS (satu sumber kebenaran
    buat label, batas panjang, dan parser)."""

    def __init__(
        self,
        title: str,
        specs: tuple[spec.InputSpec, ...],
        defaults: dict[str, str],
        on_submit_callback: Callable[[discord.Interaction, dict], Awaitable[None]],
    ) -> None:
        super().__init__(title=title[:45])
        self._on_submit_callback = on_submit_callback
        self._inputs: dict[str, discord.ui.TextInput] = {}
        for s in specs:
            default = (defaults.get(s.key) or "")[: s.max_length] or None
            text_input = discord.ui.TextInput(
                label=s.label[:45],
                style=discord.TextStyle.paragraph if s.style == "paragraph" else discord.TextStyle.short,
                required=s.required,
                max_length=s.max_length,
                placeholder=(s.placeholder or None) and s.placeholder[:100],
                default=default,
            )
            self.add_item(text_input)
            self._inputs[s.key] = text_input

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit_callback(interaction, {k: ti.value for k, ti in self._inputs.items()})

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception("Error di modal Catalog Builder.", exc_info=error)
        await _say(interaction, "Ada yang error pas nyimpen input itu. Coba lagi ya.")


class StockModal(discord.ui.Modal):
    """Restock cepat langsung dari node (gak lewat draft): aksi + jumlah."""

    def __init__(self, on_submit_callback: Callable[[discord.Interaction, str, str], Awaitable[None]]) -> None:
        super().__init__(title="Atur Stok")
        self._on_submit_callback = on_submit_callback
        self.action = discord.ui.TextInput(
            label="Aksi (tambah / kurang / set)", default="tambah", required=True, max_length=10,
        )
        self.amount = discord.ui.TextInput(
            label="Jumlah", required=True, max_length=12, placeholder="Contoh: 5000",
        )
        self.add_item(self.action)
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self._on_submit_callback(interaction, str(self.action.value), str(self.amount.value))

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.exception("Error di modal stok Catalog Builder.", exc_info=error)
        await _say(interaction, "Ada yang error pas ngatur stok. Coba lagi ya.")


async def _say(interaction: discord.Interaction, text: str) -> None:
    """Balesan ephemeral singkat, aman dipanggil walau interaction udah direspon."""
    try:
        embed = embeds.error_embed(text)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass


def _safe_emoji(value: str | None) -> str | None:
    return value if value and is_valid_emoji(value) else None


def _text(markdown: str) -> discord.ui.TextDisplay:
    return discord.ui.TextDisplay(markdown[:3800])


def _sep() -> discord.ui.Separator:
    return discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small)


# ============================================================================
# STATE
# ============================================================================

@dataclass
class State:
    screen: str = "home"          # home | node | editor | location | fields | delete
    kind: str | None = None       # kind node yang dibuka (screen node)
    entity_id: int | None = None
    page: int = 0                 # halaman daftar anak
    loc_page: int = 0             # halaman picker lokasi
    draft: spec.Draft | None = None
    back: dict | None = None      # snapshot tujuan Batal/Kembali dari editor, fields
    scope: dict | None = None     # screen fields: {"category_type_id", "group_id"}
    fields_parent: dict | None = None   # node asal layar fields (tujuan Kembali)
    delete_target: tuple | None = None   # (kind, id)
    delete_back: dict | None = None
    delete_done: dict | None = None
    notice: str | None = None


HOME = {"screen": "home", "kind": None, "entity_id": None, "scope": None, "page": 0}


class BuilderView(discord.ui.LayoutView):
    def __init__(self, owner_id: int, container: discord.ui.Container) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        self.add_item(container)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await _say(interaction, OWNER_ONLY)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        logger.exception("Error di panel Catalog Builder.", exc_info=error)
        await _say(interaction, "Ada yang error di panel. Coba klik lagi, atau buka ulang `/builder`.")


# ============================================================================
# BUILDER
# ============================================================================

class Builder:
    """Satu instance per panel yang dibuka. Nyimpen State; tiap interaksi
    ngubah State terus render ulang view dari data DB terbaru."""

    def __init__(self, owner_id: int) -> None:
        self.owner_id = owner_id
        self.state = State()

    # -- util tombol ---------------------------------------------------------

    def btn(self, handler, *, label=None, emoji=None, style=_STYLE.secondary, disabled=False) -> Btn:
        return Btn(self._guard(handler), label=label, emoji=emoji, style=style, disabled=disabled)

    def _guard(self, handler):
        async def run(interaction: discord.Interaction, *args):
            try:
                await handler(interaction, *args)
            except Exception:  # noqa: BLE001
                logger.exception("Error di handler Catalog Builder.")
                await _say(interaction, "Ada yang error. Coba lagi, atau buka ulang `/builder`.")
        return run

    def sel(self, handler, *, placeholder, options) -> Sel:
        return Sel(self._guard(handler), placeholder=placeholder, options=options)

    # -- state ---------------------------------------------------------------

    def snapshot(self) -> dict:
        s = self.state
        return {"screen": s.screen, "kind": s.kind, "entity_id": s.entity_id, "scope": s.scope, "page": s.page}

    def restore(self, snap: dict | None) -> None:
        snap = snap or HOME
        s = self.state
        s.screen, s.kind, s.entity_id = snap["screen"], snap["kind"], snap["entity_id"]
        s.scope, s.page = snap.get("scope"), snap.get("page", 0)
        if s.screen != "editor":
            s.draft = None

    def go_node(self, kind: str, entity_id: int) -> None:
        s = self.state
        s.screen, s.kind, s.entity_id, s.page, s.draft, s.scope = "node", kind, entity_id, 0, None, None

    def go_home(self) -> None:
        self.restore(HOME)

    async def parent_snapshot(self, db, kind: str, entity_id: int) -> dict:
        values = await store.load_values(db, kind, entity_id)
        if values is None:
            return dict(HOME)
        if kind == PRODUCT:
            if values.get("product_group_id"):
                return {"screen": "node", "kind": GROUP, "entity_id": values["product_group_id"], "scope": None, "page": 0}
            return {"screen": "node", "kind": TYPE, "entity_id": values["category_type_id"], "scope": None, "page": 0}
        if kind == GROUP:
            return {"screen": "node", "kind": TYPE, "entity_id": values["category_type_id"], "scope": None, "page": 0}
        if kind == TYPE:
            return {"screen": "node", "kind": CATEGORY, "entity_id": values["category_id"], "scope": None, "page": 0}
        return dict(HOME)

    # -- render --------------------------------------------------------------

    async def render(self, db) -> BuilderView:
        notice, self.state.notice = self.state.notice, None
        return BuilderView(self.owner_id, await self._build(db, notice))

    async def _build(self, db, notice) -> discord.ui.Container:
        """Container buat layar yang lagi aktif."""
        s = self.state
        if s.screen == "node":
            container = await self._render_node(db, notice)
        elif s.screen == "editor":
            container = await self._render_editor(db, notice)
        elif s.screen == "location":
            container = await self._render_location(db, notice)
        elif s.screen == "fields":
            container = await self._render_fields(db, notice)
        elif s.screen == "delete":
            container = await self._render_delete(db, notice)
        else:
            container = await self._render_home(db, notice)
        return container

    async def refresh(self, interaction: discord.Interaction, notice: str | None = None) -> None:
        if notice is not None:
            self.state.notice = notice
        view = await self.render(interaction.client.db)  # type: ignore[attr-defined]
        if interaction.response.is_done():
            await interaction.edit_original_response(view=view)
        else:
            await interaction.response.edit_message(view=view)

    def _notice(self, notice: str | None) -> list:
        return [_text(f"> {notice}")] if notice else []

    # -- daftar anak (select + paging) ----------------------------------------

    def _children_rows(self, items: list[store.Child], placeholder: str) -> list:
        s = self.state
        pages = max(1, math.ceil(len(items) / PAGE_SIZE))
        s.page = max(0, min(s.page, pages - 1))
        chunk = items[s.page * PAGE_SIZE:(s.page + 1) * PAGE_SIZE]
        options = []
        for c in chunk:
            prefix = "" if c.enabled else "\u26D4 "
            label = f"{prefix}{c.label}"[:100]
            options.append(
                discord.SelectOption(
                    label=label, value=f"{c.kind}:{c.id}", description=c.description[:100] or None,
                    emoji=_safe_emoji(c.emoji),
                )
            )
        rows = [discord.ui.ActionRow(self.sel(self._on_open_child, placeholder=placeholder, options=options))]
        if pages > 1:
            rows.append(
                discord.ui.ActionRow(
                    self.btn(self._on_page(-1), emoji="\u25C0\uFE0F", disabled=s.page <= 0),
                    self.btn(self._on_page(+1), emoji="\u25B6\uFE0F", disabled=s.page >= pages - 1),
                )
            )
            rows.append(_text(f"-# Halaman {s.page + 1}/{pages} \u00B7 {len(items)} item"))
        return rows

    def _on_page(self, delta: int):
        async def handler(interaction: discord.Interaction) -> None:
            self.state.page += delta
            await self.refresh(interaction)
        return handler

    async def _on_open_child(self, interaction: discord.Interaction, value: str) -> None:
        kind, _, raw_id = value.partition(":")
        self.go_node(kind, int(raw_id))
        await self.refresh(interaction)

    # ========================================================================
    # HOME
    # ========================================================================

    async def _render_home(self, db, notice) -> discord.ui.Container:
        ov = await store.overview(db)
        items = await store.children(db, None, None)
        lines = [
            "## \U0001F6E0\uFE0F Catalog Builder",
            f"**{ov['categories']}** kategori \u00B7 **{ov['types']}** category type \u00B7 "
            f"**{ov['groups']}** product type \u00B7 **{ov['products']}** produk",
        ]
        warnings = []
        if ov["out_of_stock"]:
            warnings.append(f"\u26A0\uFE0F {ov['out_of_stock']} produk stoknya habis")
        if ov["hidden"]:
            warnings.append(f"\u26D4 {ov['hidden']} produk gak keliatan customer")
        if warnings:
            lines.append(" \u00B7 ".join(warnings))
        children: list = [_text("\n".join(lines))]
        children += self._notice(notice)
        children.append(_sep())
        if items:
            children += self._children_rows(items, "Pilih kategori buat dibuka...")
        else:
            children.append(_text("Belum ada kategori. Mulai dengan menambah kategori pertama."))
        children.append(
            discord.ui.ActionRow(
                self.btn(self._on_new(CATEGORY), label="Kategori", emoji="\u2795", style=_STYLE.success),
                self.btn(self._on_refresh, label="Refresh", emoji="\U0001F504"),
                self.btn(self._on_close, label="Tutup", emoji="\u2716\uFE0F"),
            )
        )
        return discord.ui.Container(*children, accent_colour=COLOR_ACCENT)

    async def _on_refresh(self, interaction: discord.Interaction) -> None:
        await self.refresh(interaction)

    async def _on_close(self, interaction: discord.Interaction) -> None:
        closed = discord.ui.LayoutView(timeout=None)
        closed.add_item(
            discord.ui.Container(
                _text("## \U0001F6E0\uFE0F Catalog Builder\nPanel ditutup. Jalanin `/builder` buat buka lagi."),
                accent_colour=COLOR_PRIMARY,
            )
        )
        await interaction.response.edit_message(view=closed)

    # ========================================================================
    # NODE (Kategori / Category Type / Product Type / Produk)
    # ========================================================================

    async def _crumb(self, db, kind: str, values: dict) -> str:
        path = await store.path_for(db, values)
        name = values.get("name") or ""
        return " \u203a ".join(p for p in (path, name) if p) if kind != CATEGORY else name

    async def _render_node(self, db, notice) -> discord.ui.Container:
        s = self.state
        kind, entity_id = s.kind, s.entity_id
        values = await store.load_values(db, kind, entity_id)
        if values is None:
            self.go_home()
            return await self._render_home(db, "Item itu udah gak ada (mungkin dihapus).")

        names = await store.names_for(db, values)
        group = await store.group_row(db, values.get("product_group_id")) if kind == PRODUCT else None
        path = await store.path_for(db, values)
        preview = spec.describe(kind, values, names=names, group=group, path=path if kind == PRODUCT else None)
        crumb = await self._crumb(db, kind, values)

        children: list = [_text(f"-# \U0001F6E0\uFE0F Catalog Builder \u203a {KIND_LABEL[kind]}\n**{crumb}**")]
        children += self._notice(notice)
        children.append(_sep())
        image = (values.get("image_url") or "").strip() if kind == PRODUCT else ""
        if image and spec.valid_url(image):
            children.append(
                discord.ui.Section(_text(preview), accessory=discord.ui.Thumbnail(media=image))
            )
        else:
            children.append(_text(preview))

        if kind != PRODUCT:
            items = await store.children(db, kind, entity_id)
            children.append(_sep())
            if items:
                placeholder = {
                    CATEGORY: "Buka Category Type...",
                    TYPE: "Buka Product Type / produk...",
                    GROUP: "Buka produk...",
                }[kind]
                children += self._children_rows(items, placeholder)
            else:
                empty = {
                    CATEGORY: "Belum ada Category Type di kategori ini.",
                    TYPE: "Belum ada Product Type atau produk di sini.",
                    GROUP: "Belum ada produk di Product Type ini.",
                }[kind]
                children.append(_text(f"*{empty}*"))

        children.append(_sep())
        children += self._node_actions(kind, entity_id, values)
        accent = COLOR_PRIMARY if values.get("enabled", values.get("visible", 1)) else COLOR_WARNING
        return discord.ui.Container(*children, accent_colour=accent)

    def _node_actions(self, kind: str, entity_id: int, values: dict) -> list:
        enabled = bool(values.get("enabled", values.get("visible", 1)))
        on_label = ("Nonaktifkan", "\u26AB") if enabled else ("Aktifkan", "\U0001F7E2")
        if kind == PRODUCT:
            on_label = ("Sembunyiin", "\u26AB") if enabled else ("Tampilkan", "\U0001F7E2")
        toggle = self.btn(self._on_toggle, label=on_label[0], emoji=on_label[1])
        edit = self.btn(self._on_edit, label="Edit", emoji="\u270F\uFE0F", style=_STYLE.primary)
        up = self.btn(self._on_move(-1), emoji="\u2B06\uFE0F")
        down = self.btn(self._on_move(+1), emoji="\u2B07\uFE0F")
        preview = self.btn(self._on_preview, label="Preview", emoji="\U0001F441\uFE0F")
        delete = self.btn(self._on_delete, label="Hapus", emoji="\U0001F5D1\uFE0F", style=_STYLE.danger)
        back = self.btn(self._on_back, label="Kembali", emoji="\u2B05\uFE0F")
        home = self.btn(self._on_home, label="Home", emoji="\U0001F3E0")

        if kind == CATEGORY:
            return [
                discord.ui.ActionRow(
                    self.btn(self._on_new(TYPE), label="Category Type", emoji="\u2795", style=_STYLE.success),
                    edit, up, down, toggle,
                ),
                discord.ui.ActionRow(preview, delete, home),
            ]
        if kind == TYPE:
            return [
                discord.ui.ActionRow(
                    self.btn(self._on_new(GROUP), label="Product Type", emoji="\u2795", style=_STYLE.success),
                    self.btn(self._on_new(PRODUCT), label="Produk", emoji="\u2795", style=_STYLE.success),
                    self.btn(self._on_open_fields, label="Field", emoji="\U0001F9FE"),
                    edit, toggle,
                ),
                discord.ui.ActionRow(up, down, preview, delete),
                discord.ui.ActionRow(back, home),
            ]
        if kind == GROUP:
            return [
                discord.ui.ActionRow(
                    self.btn(self._on_new(PRODUCT), label="Produk", emoji="\u2795", style=_STYLE.success),
                    self.btn(self._on_open_fields, label="Field", emoji="\U0001F9FE"),
                    self.btn(self._on_stock, label="Stok", emoji="\U0001F4E6"),
                    edit, toggle,
                ),
                discord.ui.ActionRow(up, down, preview, delete),
                discord.ui.ActionRow(back, home),
            ]
        return [  # PRODUCT
            discord.ui.ActionRow(
                edit,
                self.btn(self._on_stock, label="Stok", emoji="\U0001F4E6"),
                self.btn(self._on_duplicate, label="Duplikat", emoji="\U0001F4CB"),
                toggle, delete,
            ),
            discord.ui.ActionRow(up, down, preview, back, home),
        ]

    # -- aksi node -------------------------------------------------------------

    async def _on_home(self, interaction: discord.Interaction) -> None:
        self.go_home()
        await self.refresh(interaction)

    async def _on_back(self, interaction: discord.Interaction) -> None:
        s = self.state
        if s.screen == "node":
            snap = await self.parent_snapshot(interaction.client.db, s.kind, s.entity_id)  # type: ignore[attr-defined]
            self.restore(snap)
        elif s.screen == "fields":
            self.restore(s.fields_parent)
        else:
            self.restore(s.back)
        await self.refresh(interaction)

    async def _on_toggle(self, interaction: discord.Interaction) -> None:
        s = self.state
        new_state = await store.toggle(interaction.client.db, s.kind, s.entity_id)  # type: ignore[attr-defined]
        if new_state is None:
            await self.refresh(interaction, "Item itu udah gak ada.")
            return
        word = ("tampil" if new_state else "disembunyiin") if s.kind == PRODUCT else ("aktif" if new_state else "nonaktif")
        await self._log(interaction, f"{KIND_LABEL[s.kind]} #{s.entity_id} sekarang **{word}**.")
        await self.refresh(interaction, f"\u2705 Status diubah jadi **{word}**.")

    def _on_move(self, direction: int):
        async def handler(interaction: discord.Interaction) -> None:
            s = self.state
            moved = await store.move(interaction.client.db, s.kind, s.entity_id, direction)  # type: ignore[attr-defined]
            await self.refresh(
                interaction,
                "\u2705 Urutan digeser." if moved else "Udah di ujung, gak bisa digeser lagi.",
            )
        return handler

    async def _on_stock(self, interaction: discord.Interaction) -> None:
        s = self.state

        async def submit(inter: discord.Interaction, action_text: str, amount_text: str) -> None:
            action = _parse_stock_action(action_text)
            amount = spec.parse_int(amount_text)
            if action is None:
                await self.refresh(inter, "\u26A0\uFE0F Aksi harus `tambah`, `kurang`, atau `set`.")
                return
            if amount is None:
                await self.refresh(inter, f"\u26A0\uFE0F Jumlah '{amount_text}' bukan bilangan bulat.")
                return
            ok, message = await store.apply_stock(inter.client.db, s.kind, s.entity_id, action, amount)  # type: ignore[attr-defined]
            if ok:
                await self._log(inter, message)
            await self.refresh(inter, ("\u2705 " if ok else "\u26A0\uFE0F ") + message)

        await interaction.response.send_modal(StockModal(submit))

    # -- preview customer -----------------------------------------------------

    async def _on_preview(self, interaction: discord.Interaction) -> None:
        """Tampilan ASLI yang dilihat customer (view yang sama dengan /shop),
        dikirim sebagai pesan ephemeral terpisah -- panel tetep terbuka."""
        from bot.database.queries import categories as categories_q
        from bot.database.queries import category_types as category_types_q
        from bot.database.queries import product_groups as product_groups_q
        from bot.database.queries import products as products_q
        from bot.database.queries import reviews as reviews_q
        from bot.ui import views

        db = interaction.client.db  # type: ignore[attr-defined]
        s = self.state
        note = "\U0001F441\uFE0F **Preview customer**"
        if s.kind == CATEGORY:
            category = await categories_q.get_category(db, s.entity_id)
            types = await category_types_q.list_category_types(db, category_id=s.entity_id, enabled_only=True)
            embed = embeds.base_embed(
                f"NOCTRA -- {category['emoji'] + ' ' if category['emoji'] else ''}{category['name']}",
                "Pilih tipe di bawah buat liat produknya.", color=COLOR_ACCENT,
            )
            if not category["enabled"]:
                note += " \u2014 kategori ini NONAKTIF, customer gak melihatnya."
            await interaction.response.send_message(
                content=note, embed=embed, view=views.CategoryTypeBrowseView(category, types), ephemeral=True
            )
        elif s.kind == TYPE:
            category_type = await category_types_q.get_category_type(db, s.entity_id)
            embed, view = await views._type_level_payload(db, category_type)
            if not category_type["enabled"]:
                note += " \u2014 Category Type ini NONAKTIF, customer gak melihatnya."
            await interaction.response.send_message(content=note, embed=embed, view=view, ephemeral=True)
        elif s.kind == GROUP:
            group = await product_groups_q.get_product_group(db, s.entity_id)
            category_type = await category_types_q.get_category_type(db, group["category_type_id"])
            embed, view = await views._group_products_payload(db, category_type, group)
            if not group["enabled"]:
                note += " \u2014 Product Type ini NONAKTIF, customer gak melihatnya."
            await interaction.response.send_message(content=note, embed=embed, view=view, ephemeral=True)
        else:
            product = await products_q.get_product(db, s.entity_id)
            group = await product_groups_q.get_product_group(db, product["product_group_id"]) if product["product_group_id"] else None
            fields = await fields_q.list_fields_for_product(db, product["category_type_id"], product["product_group_id"])
            rating = await reviews_q.get_rating_summary(db, s.entity_id)
            await interaction.response.send_message(
                view=views.ProductDetailView(product, fields, rating, group), ephemeral=True
            )

    # ========================================================================
    # DRAFT: tambah / edit / duplikat
    # ========================================================================

    def _on_new(self, kind: str):
        async def handler(interaction: discord.Interaction) -> None:
            db = interaction.client.db  # type: ignore[attr-defined]
            s = self.state
            parent: dict = {}
            if kind == TYPE:
                parent = {"category_id": s.entity_id}
            elif kind == GROUP:
                parent = {"category_type_id": s.entity_id}
            elif kind == PRODUCT:
                if s.kind == GROUP:
                    group = await store.group_row(db, s.entity_id)
                    parent = {"category_type_id": group["category_type_id"], "product_group_id": s.entity_id}
                else:
                    parent = {"category_type_id": s.entity_id}
            elif kind == FIELD:
                parent = {"category_type_id": s.scope["category_type_id"], "product_group_id": s.scope.get("group_id")}
            currency = await store.default_currency(db) if kind == PRODUCT else "IDR"
            self.state.back = self.snapshot()
            self.state.draft = spec.Draft(kind, None, spec.new_values(kind, parent=parent, default_currency=currency))
            self.state.screen = "editor"
            await self.refresh(interaction)
        return handler

    async def _on_edit(self, interaction: discord.Interaction) -> None:
        s = self.state
        values = await store.load_values(interaction.client.db, s.kind, s.entity_id)  # type: ignore[attr-defined]
        if values is None:
            await self.refresh(interaction, "Item itu udah gak ada.")
            return
        s.back = self.snapshot()
        s.draft = spec.Draft(s.kind, s.entity_id, dict(values), dict(values))
        s.screen = "editor"
        await self.refresh(interaction)

    async def _on_duplicate(self, interaction: discord.Interaction) -> None:
        s = self.state
        values = await store.load_values(interaction.client.db, PRODUCT, s.entity_id)  # type: ignore[attr-defined]
        if values is None:
            await self.refresh(interaction, "Produk itu udah gak ada.")
            return
        s.back = self.snapshot()
        s.draft = spec.Draft(
            PRODUCT, None, store.duplicate_values(values), None, note=f"Duplikat dari produk #{s.entity_id}"
        )
        s.screen = "editor"
        await self.refresh(interaction)

    # -- layar editor ------------------------------------------------------------

    async def _render_editor(self, db, notice) -> discord.ui.Container:
        d = self.state.draft
        kind, v = d.kind, d.values
        group = await store.group_row(db, v.get("product_group_id")) if kind == PRODUCT else None
        problems = spec.validate(
            kind, v, group=group, group_type_id=group["category_type_id"] if group else None
        )
        names = await store.names_for(db, v)
        path = await store.path_for(db, v)

        verb = "\u2795 Tambah" if d.is_new else "\u270F\uFE0F Edit"
        title = f"## {verb} {KIND_LABEL[kind]}"
        if not d.is_new:
            title += f" #{d.entity_id}"
        if d.note:
            title += f"\n-# {d.note}"

        children: list = [_text(f"-# \U0001F6E0\uFE0F Catalog Builder \u203a Editor\n{title}")]
        children += self._notice(notice)
        children.append(_sep())

        preview = spec.describe(kind, v, names=names, group=group, path=path if kind in (PRODUCT, FIELD) else None)
        image = (v.get("image_url") or "").strip() if kind == PRODUCT else ""
        header = _text(f"**Preview**\n{preview}")
        if image and spec.valid_url(image):
            children.append(discord.ui.Section(header, accessory=discord.ui.Thumbnail(media=image)))
        else:
            children.append(header)

        children.append(_sep())
        if d.is_new:
            status = "\u2705 **Siap disimpan.**" if not problems else "\u26A0\uFE0F **Belum bisa disimpan:**\n" + "\n".join(f"\u2022 {p}" for p in problems)
        else:
            changes = spec.diff(d, names)
            if problems:
                status = "\u26A0\uFE0F **Belum bisa disimpan:**\n" + "\n".join(f"\u2022 {p}" for p in problems)
                if changes:
                    status += f"\n\n**Perubahan ({len(changes)})**\n" + "\n".join(changes)
            elif changes:
                status = f"**Perubahan ({len(changes)})** \u2014 belum disimpan\n" + "\n".join(changes)
            else:
                status = "*Belum ada perubahan dari data yang tersimpan.*"
        children.append(_text(status))
        children.append(_sep())

        children += self._editor_actions(d, problems)
        accent = COLOR_DANGER if problems else COLOR_ACCENT
        return discord.ui.Container(*children, accent_colour=accent)

    def _form(self, form: str):
        async def handler(interaction: discord.Interaction) -> None:
            d = self.state.draft
            specs = spec.FORMS[(d.kind, form)]

            async def submit(inter: discord.Interaction, raw: dict) -> None:
                updates, errors = spec.apply_form(d.kind, form, raw)
                d.values.update(updates)
                notice = None
                if errors:
                    notice = "\u26A0\uFE0F " + " ".join(errors)
                await self.refresh(inter, notice)

            title = f"{KIND_LABEL[d.kind]}: {spec.FORM_TITLE.get(form, 'Detail')}" if d.kind != FIELD else "Field: Teks & Batas"
            await interaction.response.send_modal(
                FormModal(title, specs, spec.form_defaults(d.kind, form, d.values), submit)
            )
        return handler

    def _cycle(self, key: str, options: tuple):
        async def handler(interaction: discord.Interaction) -> None:
            d = self.state.draft
            d.values[key] = spec.cycle(options, d.values.get(key))
            await self.refresh(interaction)
        return handler

    def _flip(self, key: str):
        async def handler(interaction: discord.Interaction) -> None:
            d = self.state.draft
            d.values[key] = 0 if d.values.get(key) else 1
            await self.refresh(interaction)
        return handler

    def _editor_actions(self, d: spec.Draft, problems: list[str]) -> list:
        v, kind = d.values, d.kind
        yes = lambda x: "\u2705" if x else "\u26D4"  # noqa: E731
        rows: list = []
        if kind == CATEGORY:
            rows.append(discord.ui.ActionRow(
                self.btn(self._form("info"), label="Info", emoji="\u270F\uFE0F", style=_STYLE.primary),
                self.btn(self._flip("enabled"), label=f"Status: {'aktif' if v.get('enabled') else 'nonaktif'}"),
            ))
        elif kind == TYPE:
            rows.append(discord.ui.ActionRow(
                self.btn(self._form("info"), label="Info", emoji="\u270F\uFE0F", style=_STYLE.primary),
                self.btn(self._open_location, label="Kategori", emoji="\U0001F5C2\uFE0F"),
                self.btn(self._flip("enabled"), label=f"Status: {'aktif' if v.get('enabled') else 'nonaktif'}"),
            ))
        elif kind == GROUP:
            rows.append(discord.ui.ActionRow(
                self.btn(self._form("info"), label="Info", emoji="\u270F\uFE0F", style=_STYLE.primary),
                self.btn(self._form("stock"), label="Isi Pool", emoji="\U0001F4E6", disabled=not v.get("shared_stock")),
                self.btn(self._flip("shared_stock"), label=f"Pool: {'ya' if v.get('shared_stock') else 'tidak'}"),
                self.btn(self._cycle("stock_mode", spec.STOCK_MODES), label=f"Mode: {v.get('stock_mode')}"),
                self.btn(self._flip("enabled"), label=f"Status: {'aktif' if v.get('enabled') else 'nonaktif'}"),
            ))
        elif kind == PRODUCT:
            rows.append(discord.ui.ActionRow(
                self.btn(self._form("info"), label="Info", emoji="\u270F\uFE0F", style=_STYLE.primary),
                self.btn(self._form("price"), label="Harga", emoji="\U0001F4B0", style=_STYLE.primary),
                self.btn(self._form("stock"), label="Stok", emoji="\U0001F4E6", style=_STYLE.primary),
                self.btn(self._open_location, label="Lokasi", emoji="\U0001F5C2\uFE0F"),
                self.btn(self._on_draft_preview, label="Preview", emoji="\U0001F441\uFE0F"),
            ))
            rows.append(discord.ui.ActionRow(
                self.btn(self._cycle("product_type", spec.PRODUCT_TYPES), label=f"Kirim: {v.get('product_type')}"),
                self.btn(self._cycle("stock_type", spec.STOCK_TYPES), label=f"Stok: {v.get('stock_type')}"),
                self.btn(self._cycle("stock_mode", spec.STOCK_MODES), label=f"Mode: {v.get('stock_mode')}"),
                self.btn(self._cycle("discount_type", spec.DISCOUNT_TYPES), label=f"Diskon: {spec.DISCOUNT_WORD[v.get('discount_type')]}"),
                self.btn(self._flip("visible"), label=f"{yes(v.get('visible'))} {'Tampil' if v.get('visible') else 'Sembunyi'}"),
            ))
        elif kind == FIELD:
            rows.append(discord.ui.ActionRow(
                self.btn(self._form("info"), label="Teks & Batas", emoji="\u270F\uFE0F", style=_STYLE.primary),
                self.btn(self._cycle("field_type", spec.FIELD_TYPES), label=f"Jenis: {v.get('field_type')}"),
                self.btn(self._cycle("validation", spec.VALIDATIONS), label=f"Validasi: {v.get('validation')}"),
                self.btn(self._flip("required"), label="Wajib" if v.get("required") else "Opsional"),
            ))

        no_change = (not d.is_new) and not d.changed_keys()
        final = [
            self.btn(self._on_save, label="Simpan", emoji="\u2705", style=_STYLE.success,
                     disabled=bool(problems) or no_change),
            self.btn(self._on_reset, label="Reset", emoji="\u21A9\uFE0F"),
            self.btn(self._on_cancel, label="Batal", emoji="\u2716\uFE0F"),
        ]
        if kind == FIELD and not d.is_new:
            final.append(self.btn(self._on_delete_field, label="Hapus", emoji="\U0001F5D1\uFE0F", style=_STYLE.danger))
        rows.append(discord.ui.ActionRow(*final))
        return rows

    async def _on_reset(self, interaction: discord.Interaction) -> None:
        d = self.state.draft
        if d.original is not None:
            d.values = dict(d.original)
        else:
            parent = {k: d.values.get(k) for k in ("category_id", "category_type_id", "product_group_id")}
            fresh = spec.new_values(d.kind, parent=parent, default_currency=d.values.get("currency_label") or "IDR")
            d.values = fresh
        await self.refresh(interaction, "\u21A9\uFE0F Draft dikembalikan.")

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self.restore(self.state.back)
        await self.refresh(interaction, "Dibatalin -- gak ada yang disimpan.")

    async def _on_save(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        s = self.state
        d = s.draft
        group = await store.group_row(db, d.values.get("product_group_id")) if d.kind == PRODUCT else None
        problems = spec.validate(d.kind, d.values, group=group, group_type_id=group["category_type_id"] if group else None)
        if problems:
            await self.refresh(interaction, "\u26A0\uFE0F Belum bisa disimpan: " + problems[0])
            return
        names = await store.names_for(db, d.values)
        changes = spec.diff(d, names)
        was_new = d.is_new
        entity_id = await store.save(db, d)
        name = d.values.get("label") if d.kind == FIELD else d.values.get("name")
        verb = "dibuat" if was_new else "diubah"
        log = f"{KIND_LABEL[d.kind]} **{name}** (#{entity_id}) {verb}."
        if changes:
            log += "\n" + "\n".join(changes)
        await self._log(interaction, log)

        if d.kind == FIELD:
            self.restore(s.back)
        else:
            self.go_node(d.kind, entity_id)
        await self.refresh(interaction, f"\u2705 {KIND_LABEL[d.kind]} **{name}** berhasil {verb}.")

    async def _on_draft_preview(self, interaction: discord.Interaction) -> None:
        """Preview produk dari DRAFT (belum disimpan) sebagai kartu detail
        customer -- komponen yang sama dengan yang dipakai /shop."""
        db = interaction.client.db  # type: ignore[attr-defined]
        d = self.state.draft
        v = dict(d.values)
        v["id"] = d.entity_id or 0
        v["base_price"] = v.get("base_price") or 0
        v["discount_value"] = v.get("discount_value") or 0
        v["stock_cost"] = v.get("stock_cost") or 1
        if v.get("image_url") and not spec.valid_url(v["image_url"]):
            v["image_url"] = None
        v["name"] = v.get("name") or "(belum diberi nama)"
        group = await store.group_row(db, v.get("product_group_id"))
        fields = []
        if v.get("category_type_id"):
            fields = await fields_q.list_fields_for_product(db, v["category_type_id"], v.get("product_group_id"))
        view = discord.ui.LayoutView(timeout=300)
        view.add_item(components.product_detail_container(v, fields, {"total": 0, "average": 0}, group, 1))
        await interaction.response.send_message(view=view, ephemeral=True)

    # ========================================================================
    # LOKASI (picker induk)
    # ========================================================================

    async def _open_location(self, interaction: discord.Interaction) -> None:
        self.state.screen = "location"
        self.state.loc_page = 0
        await self.refresh(interaction)

    async def _render_location(self, db, notice) -> discord.ui.Container:
        s = self.state
        d = s.draft
        children: list = [_text(f"-# \U0001F6E0\uFE0F Catalog Builder \u203a Editor \u203a Lokasi\n## \U0001F5C2\uFE0F Pilih Lokasi {KIND_LABEL[d.kind]}")]
        children += self._notice(notice)
        children.append(_sep())

        if d.kind == PRODUCT:
            types = await store.list_types_flat(db)
            pages = max(1, math.ceil(len(types) / PAGE_SIZE))
            s.loc_page = max(0, min(s.loc_page, pages - 1))
            chunk = types[s.loc_page * PAGE_SIZE:(s.loc_page + 1) * PAGE_SIZE]
            current = d.values.get("category_type_id")
            if not types:
                children.append(_text("*Belum ada Category Type. Bikin dulu di Home.*"))
            else:
                options = [
                    discord.SelectOption(
                        label=f"{t['category_name']} \u203a {t['name']}"[:100], value=str(t["id"]),
                        emoji=_safe_emoji(t["emoji"]), default=(t["id"] == current),
                    )
                    for t in chunk
                ]
                children.append(discord.ui.ActionRow(
                    self.sel(self._on_pick_type, placeholder="Pilih Category Type...", options=options)
                ))
                if pages > 1:
                    children.append(discord.ui.ActionRow(
                        self.btn(self._on_loc_page(-1), emoji="\u25C0\uFE0F", disabled=s.loc_page <= 0),
                        self.btn(self._on_loc_page(+1), emoji="\u25B6\uFE0F", disabled=s.loc_page >= pages - 1),
                    ))
            if current:
                from bot.database.queries import product_groups as product_groups_q

                groups = await product_groups_q.list_product_groups(db, category_type_id=current)
                if groups:
                    cur_group = d.values.get("product_group_id")
                    options = [discord.SelectOption(label="(Tanpa Product Type)", value="0", default=not cur_group)]
                    options += [
                        discord.SelectOption(
                            label=g["name"][:100], value=str(g["id"]), emoji=_safe_emoji(g["emoji"]),
                            default=(g["id"] == cur_group),
                        )
                        for g in groups[:24]
                    ]
                    children.append(discord.ui.ActionRow(
                        self.sel(self._on_pick_group, placeholder="Pilih Product Type (opsional)...", options=options)
                    ))
                else:
                    children.append(_text("*Category Type ini belum punya Product Type -- produk langsung di bawah tipe.*"))
        else:  # TYPE -> pilih kategori induk
            from bot.database.queries import categories as categories_q

            cats = await categories_q.list_categories(db)
            current = d.values.get("category_id")
            options = [
                discord.SelectOption(label=c["name"][:100], value=str(c["id"]),
                                     emoji=_safe_emoji(c["emoji"]), default=(c["id"] == current))
                for c in cats[:PAGE_SIZE]
            ]
            children.append(discord.ui.ActionRow(
                self.sel(self._on_pick_category, placeholder="Pilih kategori induk...", options=options)
            ))
        path = await store.path_for(db, d.values)
        children.append(_text(f"**Sekarang:** {path or '*(belum dipilih)*'}"))
        children.append(discord.ui.ActionRow(
            self.btn(self._on_location_done, label="Selesai", emoji="\u2705", style=_STYLE.success)
        ))
        return discord.ui.Container(*children, accent_colour=COLOR_ACCENT)

    def _on_loc_page(self, delta: int):
        async def handler(interaction: discord.Interaction) -> None:
            self.state.loc_page += delta
            await self.refresh(interaction)
        return handler

    async def _on_pick_type(self, interaction: discord.Interaction, value: str) -> None:
        d = self.state.draft
        type_id = int(value)
        group = await store.group_row(interaction.client.db, d.values.get("product_group_id"))  # type: ignore[attr-defined]
        d.values["category_type_id"] = type_id
        if group and group["category_type_id"] != type_id:
            d.values["product_group_id"] = None   # Product Type lama bukan milik tipe baru
        await self.refresh(interaction)

    async def _on_pick_group(self, interaction: discord.Interaction, value: str) -> None:
        self.state.draft.values["product_group_id"] = int(value) or None
        await self.refresh(interaction)

    async def _on_pick_category(self, interaction: discord.Interaction, value: str) -> None:
        self.state.draft.values["category_id"] = int(value)
        await self.refresh(interaction)

    async def _on_location_done(self, interaction: discord.Interaction) -> None:
        self.state.screen = "editor"
        await self.refresh(interaction)

    # ========================================================================
    # FIELD CHECKOUT
    # ========================================================================

    async def _on_open_fields(self, interaction: discord.Interaction) -> None:
        s = self.state
        db = interaction.client.db  # type: ignore[attr-defined]
        if s.kind == GROUP:
            group = await store.group_row(db, s.entity_id)
            scope = {"category_type_id": group["category_type_id"], "group_id": s.entity_id}
        else:
            scope = {"category_type_id": s.entity_id, "group_id": None}
        s.fields_parent = self.snapshot()
        s.back = dict(s.fields_parent)
        s.screen, s.scope, s.page = "fields", scope, 0
        await self.refresh(interaction)

    async def _render_fields(self, db, notice) -> discord.ui.Container:
        s = self.state
        scope = s.scope
        rows = await store.list_scope_fields(db, group_id=scope.get("group_id"), category_type_id=scope["category_type_id"])
        if scope.get("group_id"):
            group = await store.group_row(db, scope["group_id"])
            title = f"## \U0001F9FE Field Checkout \u2014 Product Type {group['name']}"
            inherited = await fields_q.list_fields(db, scope["category_type_id"])
            sub = (
                f"Field TAMBAHAN khusus Product Type ini. Ditanyain **setelah** "
                f"{len(inherited)} field dari Category Type-nya."
            )
        else:
            from bot.database.queries import category_types as category_types_q

            ct = await category_types_q.get_category_type(db, scope["category_type_id"])
            title = f"## \U0001F9FE Field Checkout \u2014 {ct['name']}"
            sub = "Berlaku buat SEMUA produk di Category Type ini."
        children: list = [_text(f"-# \U0001F6E0\uFE0F Catalog Builder \u203a Field\n{title}\n{sub}")]
        children += self._notice(notice)
        children.append(_sep())
        if rows:
            lines = [
                f"`#{r['id']}` **{r['label']}** \u00B7 {r['field_type']} \u00B7 "
                f"{'wajib' if r['required'] else 'opsional'} \u00B7 {r['min_length']}-{r['max_length']} \u00B7 {r['validation']}"
                for r in rows
            ]
            children.append(_text("\n".join(lines)))
            options = [
                discord.SelectOption(label=r["label"][:100], value=str(r["id"]),
                                     description=f"{r['field_type']} \u00B7 {'wajib' if r['required'] else 'opsional'}")
                for r in rows[:PAGE_SIZE]
            ]
            children.append(discord.ui.ActionRow(
                self.sel(self._on_pick_field, placeholder="Pilih field buat diedit...", options=options)
            ))
        else:
            children.append(_text("*Belum ada field di sini.*"))
        children.append(discord.ui.ActionRow(
            self.btn(self._on_new(FIELD), label="Tambah Field", emoji="\u2795", style=_STYLE.success),
            self.btn(self._on_back, label="Kembali", emoji="\u2B05\uFE0F"),
            self.btn(self._on_home, label="Home", emoji="\U0001F3E0"),
        ))
        return discord.ui.Container(*children, accent_colour=COLOR_PRIMARY)

    async def _on_pick_field(self, interaction: discord.Interaction, value: str) -> None:
        s = self.state
        values = await store.load_values(interaction.client.db, FIELD, int(value))  # type: ignore[attr-defined]
        if values is None:
            await self.refresh(interaction, "Field itu udah gak ada.")
            return
        s.back = self.snapshot()
        s.draft = spec.Draft(FIELD, int(value), dict(values), dict(values))
        s.screen = "editor"
        await self.refresh(interaction)

    # ========================================================================
    # HAPUS
    # ========================================================================

    async def _on_delete(self, interaction: discord.Interaction) -> None:
        s = self.state
        s.delete_target = (s.kind, s.entity_id)
        s.delete_back = self.snapshot()
        s.delete_done = await self.parent_snapshot(interaction.client.db, s.kind, s.entity_id)  # type: ignore[attr-defined]
        s.screen = "delete"
        await self.refresh(interaction)

    async def _on_delete_field(self, interaction: discord.Interaction) -> None:
        s = self.state
        s.delete_target = (FIELD, s.draft.entity_id)
        s.delete_back = self.snapshot()      # balik ke editor field (draft masih ada)
        s.delete_done = dict(s.back)         # abis hapus -> daftar field
        s.screen = "delete"
        await self.refresh(interaction)

    async def _render_delete(self, db, notice) -> discord.ui.Container:
        kind, entity_id = self.state.delete_target
        info = await store.delete_info(db, kind, entity_id)
        if info is None:
            self.restore(self.state.delete_done)
            return await self._build(db, "Item itu udah gak ada.")
        children: list = [_text(f"-# \U0001F6E0\uFE0F Catalog Builder \u203a Hapus\n## \U0001F5D1\uFE0F Hapus {KIND_LABEL[kind]}\n**{info.name}**")]
        children += self._notice(notice)
        children.append(_sep())
        if info.blocked:
            children.append(_text(f"\u26D4 {info.blocked}"))
        else:
            if info.impact:
                lines = ["Yang bakal ikut kehapus / berubah:"] + [f"\u2022 {x}" for x in info.impact]
            else:
                lines = ["Gak ada data lain yang kena."]
            children.append(_text("\u26A0\uFE0F **Penghapusan permanen, gak bisa dibalikin.**\n" + "\n".join(lines)))
        buttons = []
        if info.blocked:
            if kind in (CATEGORY, TYPE, GROUP, PRODUCT):
                values = await store.load_values(db, kind, entity_id)
                is_on = bool(values.get("enabled", values.get("visible", 1))) if values else False
                if is_on:
                    buttons.append(self.btn(self._on_disable_instead, label="Nonaktifkan / Sembunyiin saja", emoji="\u26AB", style=_STYLE.primary))
        else:
            buttons.append(self.btn(self._on_confirm_delete, label="Ya, hapus permanen", emoji="\U0001F5D1\uFE0F", style=_STYLE.danger))
        buttons.append(self.btn(self._on_delete_cancel, label="Batal", emoji="\u2716\uFE0F"))
        children.append(discord.ui.ActionRow(*buttons))
        return discord.ui.Container(*children, accent_colour=COLOR_DANGER)

    async def _on_delete_cancel(self, interaction: discord.Interaction) -> None:
        self.restore(self.state.delete_back)
        await self.refresh(interaction, "Dibatalin -- gak ada yang dihapus.")

    async def _on_disable_instead(self, interaction: discord.Interaction) -> None:
        s = self.state
        kind, entity_id = s.delete_target
        await store.toggle(interaction.client.db, kind, entity_id)  # type: ignore[attr-defined]
        await self._log(interaction, f"{KIND_LABEL[kind]} #{entity_id} dinonaktifkan (gantinya dihapus).")
        self.restore(s.delete_back)
        await self.refresh(interaction, "\u2705 Dinonaktifkan / disembunyiin dari /shop. Riwayat tetap aman.")

    async def _on_confirm_delete(self, interaction: discord.Interaction) -> None:
        s = self.state
        db = interaction.client.db  # type: ignore[attr-defined]
        kind, entity_id = s.delete_target
        info = await store.delete_info(db, kind, entity_id)
        if info is None:
            self.restore(s.delete_done)
            await self.refresh(interaction, "Item itu udah gak ada.")
            return
        if info.blocked:   # data berubah sejak layar ini dibuka
            await self.refresh(interaction)
            return
        ok, message = await store.delete(db, kind, entity_id)
        if not ok:
            self.restore(s.delete_back)
            await self.refresh(interaction, "\u26A0\uFE0F " + (message or "Gagal menghapus."))
            return
        await self._log(interaction, f"{KIND_LABEL[kind]} **{info.name}** (#{entity_id}) dihapus.")
        self.restore(s.delete_done)
        await self.refresh(interaction, f"\U0001F5D1\uFE0F {KIND_LABEL[kind]} **{info.name}** dihapus.")

    # ========================================================================
    # LOG
    # ========================================================================

    async def _log(self, interaction: discord.Interaction, text: str) -> None:
        """Catat ke log aktivitas staff (best-effort, gak boleh ngeganggu aksinya)."""
        try:
            from bot.utils import activity_log

            await activity_log.log_activity(interaction.client, interaction.user, "Katalog Diubah (Builder)", text)
        except Exception:  # noqa: BLE001
            logger.warning("Gagal nyatet log aktivitas Catalog Builder.")


def _parse_stock_action(text: str) -> str | None:
    t = (text or "").strip().lower()
    if t.startswith(("t", "a", "+")):
        return "add"
    if t.startswith(("k", "c", "-")):
        return "subtract"
    if t.startswith(("s", "=")):
        return "set"
    return None
