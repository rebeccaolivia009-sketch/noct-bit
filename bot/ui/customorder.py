"""
Panel setting "Produk Lainnya" (/customorder): pratinjau persis yang dilihat
customer di atas, kontrol di bawah. Semua perubahan lewat draft; database
baru disentuh pas Simpan. Pola yang sama dengan panel Sticky/Auto Reaction.

Field form yang diisi customer diatur lewat Catalog Builder (tombol "Atur
Field" ngebuka Builder langsung di layar field-nya) -- jadi gak ada editor
field baru, dan alur checkout-nya sama persis dengan produk biasa.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import discord

from bot.core.logger import logger
from bot.core.theme import COLOR_ACCENT, COLOR_DANGER, COLOR_PRIMARY
from bot.database.queries import fields as fields_q
from bot.database.queries import products as products_q
from bot.ui import embeds
from bot.ui.builder import HOME, Btn, Builder, FormModal
from bot.utils import builder_store as store
from bot.utils import custom_order as co
from bot.utils.helpers import format_price

_STYLE = discord.ButtonStyle
OWNER_ONLY = "Panel ini punya staff lain -- jalanin `/customorder` buat buka panelmu sendiri."

KEY_LABEL = {
    "enabled": "Status", "label": "Nama", "description": "Deskripsi", "emoji": "Emoji",
    "price_mode": "Mode harga", "fixed_price": "Harga tetap", "min_amount": "Nominal minimal",
    "max_amount": "Nominal maksimal", "price_label": "Label nominal", "currency_label": "Mata uang",
}


async def _say(interaction: discord.Interaction, text: str) -> None:
    try:
        embed = embeds.error_embed(text)
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass


def _fmt(key: str, value) -> str:
    if key == "enabled":
        return "aktif" if value else "nonaktif"
    if key == "price_mode":
        return co.MODE_LABEL.get(value, str(value))
    if value in (None, "", 0, 0.0):
        return "-"
    return str(value)[:50]


def _norm(value):
    return None if value in ("", None, 0, 0.0) else value


def diff(original: dict | None, values: dict) -> list[str]:
    if original is None:
        return []
    return [
        f"\u2022 **{KEY_LABEL[k]}**: `{_fmt(k, original.get(k))}` \u2192 `{_fmt(k, values.get(k))}`"
        for k in KEY_LABEL if _norm(original.get(k)) != _norm(values.get(k))
    ]


class CustomOrderPanelView(discord.ui.LayoutView):
    def __init__(self, owner_id: int, items: list) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id
        for item in items:
            self.add_item(item)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await _say(interaction, OWNER_ONLY)
            return False
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        logger.exception("Error di panel Produk Lainnya.", exc_info=error)
        await _say(interaction, "Ada yang error di panel. Coba lagi, atau buka ulang `/customorder`.")


class CustomOrderPanel:
    def __init__(self, owner_id: int, cfg: co.Config) -> None:
        self.owner_id = owner_id
        self.product_id = cfg.product_id
        # "Sudah tersimpan" = barisnya ada di DB. Produk sistemnya bisa aja
        # hilang (kehapus -> product_id jadi NULL); pengaturannya tetap dibaca.
        self.existing = cfg.configured
        self.original = co.values_from_config(cfg) if self.existing else None
        self.values = dict(self.original) if self.existing else co.defaults()
        self.notice: str | None = None

    def btn(self, handler, *, label=None, emoji=None, style=_STYLE.secondary, disabled=False) -> Btn:
        return Btn(self._guard(handler), label=label, emoji=emoji, style=style, disabled=disabled)

    def _guard(self, handler: Callable[[discord.Interaction], Awaitable[None]]):
        async def run(interaction: discord.Interaction) -> None:
            try:
                await handler(interaction)
            except Exception:  # noqa: BLE001
                logger.exception("Error di handler panel Produk Lainnya.")
                await _say(interaction, "Ada yang error. Coba lagi, atau buka ulang `/customorder`.")
        return run

    # -- render ----------------------------------------------------------------

    async def _form_fields(self, db) -> list:
        if not self.product_id:
            return []
        product = await products_q.get_product(db, self.product_id)
        if product is None:
            return []
        return list(await fields_q.list_fields(db, product["category_type_id"]))

    async def _preview(self, db) -> discord.ui.Container:
        v = self.values
        currency = v.get("currency_label") or await store.default_currency(db)
        title = f"{v['emoji'] + ' ' if v.get('emoji') else ''}{(v.get('label') or '').strip() or '*(belum diberi nama)*'}"
        lines = [f"## {title}", "-# Pratinjau tombol & form yang dilihat customer", v.get("description") or "*Belum ada deskripsi.*", ""]

        steps: list[str] = []
        if v["price_mode"] == "input":
            hint = []
            if v.get("min_amount"):
                hint.append(f"min {format_price(float(v['min_amount']), currency)}")
            if v.get("max_amount"):
                hint.append(f"maks {format_price(float(v['max_amount']), currency)}")
            steps.append(f"**{v.get('price_label') or co.DEFAULT_PRICE_LABEL}** \u2014 wajib \u00B7 nominal" + (f" ({', '.join(hint)})" if hint else ""))
        for f in await self._form_fields(db):
            steps.append(f"**{f['label']}** \u2014 {'wajib' if f['required'] else 'opsional'} \u00B7 {f['min_length']}-{f['max_length']} karakter")
        lines.append("**Form yang diisi customer**")
        if steps:
            lines.extend(f"{i}. {text}" for i, text in enumerate(steps, 1))
        else:
            lines.append("*Belum ada field -- tambahin lewat tombol Atur Field.*")
        lines.append("")
        if v["price_mode"] == "fixed":
            lines.append(f"**Harga:** {format_price(float(v.get('fixed_price') or 0), currency)} (tetap)")
        else:
            lines.append("**Harga:** customer isi sendiri di form")
        return discord.ui.Container(
            discord.ui.TextDisplay("\n".join(lines)),
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.small),
            discord.ui.TextDisplay(
                "-# Alurnya sama kayak produk lain: DM \u2192 isi form \u2192 pilih pembayaran \u2192 order masuk ke staff."
            ),
            accent_colour=COLOR_ACCENT,
        )

    async def render(self, bot) -> CustomOrderPanelView:
        notice, self.notice = self.notice, None
        v = self.values
        problems = co.validate(v)
        changes = diff(self.original, v)
        enabled = bool(v["enabled"])

        origin = "\u270F\uFE0F Sudah tersimpan" if self.existing else "\u2795 Belum disimpan"
        head = [
            "## \U0001F4DD Produk Lainnya \u2014 Pengaturan",
            f"{origin} \u00B7 Status: **{'aktif' if enabled else 'nonaktif'}**",
        ]
        if notice:
            head.append(f"> {notice}")
        controls: list = [discord.ui.TextDisplay("\n".join(head)), discord.ui.Separator(visible=True)]
        if problems:
            controls.append(discord.ui.TextDisplay("\u26A0\uFE0F **Belum bisa disimpan:**\n" + "\n".join(f"\u2022 {p}" for p in problems)))
        elif self.existing and changes:
            controls.append(discord.ui.TextDisplay(f"**Perubahan ({len(changes)})** \u2014 belum disimpan\n" + "\n".join(changes)))
        elif self.existing:
            controls.append(discord.ui.TextDisplay("*Belum ada perubahan dari yang tersimpan.*"))
        else:
            controls.append(discord.ui.TextDisplay("\u2705 **Siap disimpan.** Setelah itu, tombol bakal muncul di panel order (\u00B110 detik)."))
        if self.existing and not await self._product_alive(bot.db):
            controls.append(discord.ui.TextDisplay("\u26A0\uFE0F Produk sistemnya hilang (kehapus?). Klik Simpan buat bikin ulang."))

        controls.append(discord.ui.ActionRow(
            self.btn(self._form("text"), label="Teks", emoji="\u270F\uFE0F", style=_STYLE.primary),
            self.btn(self._form("price"), label="Harga", emoji="\U0001F4B0", style=_STYLE.primary),
            self.btn(self._cycle_mode, label=f"Mode: {co.MODE_LABEL[v['price_mode']]}"),
            self.btn(self._open_fields, label="Atur Field", emoji="\U0001F9FE", disabled=not self.product_id),
            self.btn(self._toggle, label=f"Status: {'aktif' if enabled else 'nonaktif'}"),
        ))
        no_change = self.existing and not changes
        controls.append(discord.ui.ActionRow(
            self.btn(self._save, label="Simpan", emoji="\u2705", style=_STYLE.success, disabled=bool(problems) or no_change),
            self.btn(self._close, label="Tutup", emoji="\u2716\uFE0F"),
        ))
        if not self.existing:
            controls.append(discord.ui.TextDisplay("-# Tombol Atur Field aktif setelah pengaturan pertama disimpan."))
        return CustomOrderPanelView(self.owner_id, [
            await self._preview(bot.db),
            discord.ui.Container(*controls, accent_colour=COLOR_DANGER if problems else COLOR_PRIMARY),
        ])

    async def _product_alive(self, db) -> bool:
        return bool(self.product_id) and await products_q.get_product(db, self.product_id) is not None

    async def refresh(self, interaction: discord.Interaction, notice: str | None = None) -> None:
        if notice is not None:
            self.notice = notice
        view = await self.render(interaction.client)
        if interaction.response.is_done():
            await interaction.edit_original_response(view=view)
        else:
            await interaction.response.edit_message(view=view)

    # -- aksi ------------------------------------------------------------------

    def _form(self, form: str):
        async def handler(interaction: discord.Interaction) -> None:
            async def submit(inter: discord.Interaction, raw: dict) -> None:
                updates, errors = co.apply_form(form, raw)
                self.values.update(updates)
                await self.refresh(inter, ("\u26A0\uFE0F " + " ".join(errors)) if errors else None)

            await interaction.response.send_modal(
                FormModal(co.FORM_TITLE[form], co.FORMS[form], co.form_defaults(form, self.values), submit)
            )
        return handler

    async def _cycle_mode(self, interaction: discord.Interaction) -> None:
        self.values["price_mode"] = "fixed" if self.values["price_mode"] == "input" else "input"
        await self.refresh(interaction)

    async def _toggle(self, interaction: discord.Interaction) -> None:
        self.values["enabled"] = 0 if self.values["enabled"] else 1
        await self.refresh(interaction)

    async def _open_fields(self, interaction: discord.Interaction) -> None:
        """Buka Catalog Builder langsung di layar field checkout buat form ini."""
        db = interaction.client.db  # type: ignore[attr-defined]
        product = await products_q.get_product(db, self.product_id) if self.product_id else None
        if product is None:
            await self.refresh(interaction, "\u26A0\uFE0F Produk sistemnya belum ada -- Simpan dulu.")
            return
        builder = Builder(interaction.user.id)
        builder.state.screen = "fields"
        builder.state.scope = {"category_type_id": int(product["category_type_id"]), "group_id": None}
        builder.state.fields_parent = dict(HOME)
        builder.state.back = dict(HOME)
        await interaction.response.send_message(view=await builder.render(db), ephemeral=True)

    async def _save(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        problems = co.validate(self.values)
        if problems:
            await self.refresh(interaction, "\u26A0\uFE0F Belum bisa disimpan: " + problems[0])
            return
        cfg = await co.save(db, self.values, currency_fallback=await store.default_currency(db))
        self.product_id = cfg.product_id
        self.existing = True
        self.values = co.values_from_config(cfg)
        self.original = dict(self.values)
        if cfg.enabled:
            await self.refresh(interaction, "\u2705 Tersimpan. Tombol bakal muncul di panel order dan /shop (\u00B110 detik). Atur field form lewat tombol Atur Field.")
        else:
            await self.refresh(interaction, "\u2705 Tersimpan, tapi nonaktif -- tombolnya disembunyiin dari customer.")

    async def _close(self, interaction: discord.Interaction) -> None:
        closed = discord.ui.LayoutView(timeout=None)
        closed.add_item(discord.ui.Container(
            discord.ui.TextDisplay("## \U0001F4DD Produk Lainnya\nPanel ditutup. Jalanin `/customorder` buat buka lagi."),
            accent_colour=COLOR_PRIMARY,
        ))
        await interaction.response.edit_message(view=closed)
