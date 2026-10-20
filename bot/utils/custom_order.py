"""
"Produk Lainnya": order produk yang GAK ada di katalog.

Gimana ini nyambung ke alur yang udah ada:
  * Produknya produk SISTEM tersembunyi (visible = 0, di kategori yang
    nonaktif) -- jadi gak muncul di /shop dan gak bisa masuk keranjang,
    tapi tetep produk beneran, jadi order, invoice, staff log, review,
    stok (unlimited), dan statistik jalan tanpa cabang khusus.
  * Form yang diisi customer = field checkout milik Category Type
    produk sistem itu, diatur lewat Catalog Builder (/customorder -> Atur Field).
  * Harga: mode `input` -- customer ngisi NOMINAL sendiri di form (field
    pertama, otomatis ditambahin di sini, batas min/maks dari pengaturan);
    mode `fixed` -- harga tetap dari pengaturan.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.database.core import Database
from bot.database.queries import categories as categories_q
from bot.database.queries import category_types as category_types_q
from bot.database.queries import custom_order as custom_order_q
from bot.database.queries import products as products_q
from bot.utils.builder_spec import InputSpec, money_text, parse_money
from bot.utils.helpers import format_price
from bot.utils.validators import is_valid_emoji

PRICE_FIELD_ID = -1          # id field "nominal" sintetis (gak ada di product_fields)
MODES = ("input", "fixed")
MODE_LABEL = {"input": "Customer isi nominal", "fixed": "Harga tetap"}

DEFAULT_LABEL = "Produk Lainnya"
DEFAULT_DESCRIPTION = (
    "Pesen produk apa pun yang gak ada di katalog -- isi form-nya, nanti tim kami yang proses."
)
DEFAULT_PRICE_LABEL = "Nominal yang mau dibayar"
SYSTEM_CATEGORY_NAME = "Produk Lainnya (sistem -- jangan dihapus)"
SYSTEM_TYPE_NAME = "Form Produk Lainnya"
MAX_LABEL = 40
MAX_DESCRIPTION = 300


@dataclass(frozen=True)
class Config:
    enabled: bool = False
    product_id: int | None = None
    label: str = DEFAULT_LABEL
    description: str | None = DEFAULT_DESCRIPTION
    emoji: str | None = None
    price_mode: str = "input"
    fixed_price: float = 0.0
    min_amount: float | None = None
    max_amount: float | None = None
    price_label: str = DEFAULT_PRICE_LABEL
    currency: str | None = None
    configured: bool = False     # baris pengaturan udah ada di DB (walau produk sistemnya hilang)

    @property
    def input_mode(self) -> bool:
        return self.price_mode == "input"


def _from_row(row) -> Config:
    if row is None:
        return Config()
    return Config(
        enabled=bool(row["enabled"]), product_id=row["product_id"], label=row["label"] or DEFAULT_LABEL,
        description=row["description"], emoji=row["emoji"], price_mode=row["price_mode"] or "input",
        fixed_price=float(row["fixed_price"] or 0), min_amount=row["min_amount"], max_amount=row["max_amount"],
        price_label=row["price_label"] or DEFAULT_PRICE_LABEL, currency=row["currency_label"],
        configured=True,
    )


async def load(db: Database) -> Config:
    return _from_row(await custom_order_q.get_config(db))


async def active(db: Database) -> Config | None:
    """Config kalau fitur nyala DAN produk sistemnya masih ada, selain itu None."""
    cfg = await load(db)
    if not cfg.enabled or cfg.product_id is None:
        return None
    if await products_q.get_product(db, cfg.product_id) is None:
        return None
    return cfg


async def is_custom_product(db: Database, product_id: int) -> bool:
    cfg = await load(db)
    return cfg.product_id is not None and int(product_id) == cfg.product_id


# ============================================================================
# FIELD NOMINAL & PARSING
# ============================================================================

def price_field(cfg: Config) -> dict:
    """Field "nominal" sintetis -- ditaruh PALING ATAS form (mode input)."""
    hint = []
    if cfg.min_amount:
        hint.append(f"min {parse_text(cfg.min_amount)}")
    if cfg.max_amount:
        hint.append(f"maks {parse_text(cfg.max_amount)}")
    placeholder = "Contoh: 150000" + (f" ({', '.join(hint)})" if hint else "")
    return {
        "id": PRICE_FIELD_ID, "label": cfg.price_label[:45], "field_type": "custom", "required": 1,
        "placeholder": placeholder[:100], "min_length": 1, "max_length": 15, "validation": "none",
    }


def parse_text(amount: float) -> str:
    return str(int(amount)) if float(amount) == int(amount) else f"{amount:g}"


def parse_amount(cfg: Config, text: str) -> tuple[float | None, str | None]:
    """Teks nominal dari customer -> (angka, pesan error). Terima '150000',
    '150.000', 'Rp 150.000'."""
    amount = parse_money(text)
    if amount is None or amount <= 0:
        return None, f"{cfg.price_label}: isi dengan angka lebih dari 0 (contoh 150000)."
    currency = cfg.currency or ""
    if cfg.min_amount and amount < cfg.min_amount:
        return None, f"{cfg.price_label} minimal {format_price(cfg.min_amount, currency).strip()}."
    if cfg.max_amount and amount > cfg.max_amount:
        return None, f"{cfg.price_label} maksimal {format_price(cfg.max_amount, currency).strip()}."
    return amount, None


# ============================================================================
# DRAFT (panel /customorder)
# ============================================================================

FORMS: dict[str, tuple[InputSpec, ...]] = {
    "text": (
        InputSpec("label", "Nama (tombol & produk)", required=True, max_length=MAX_LABEL,
                  placeholder="Contoh: Produk Lainnya"),
        InputSpec("description", "Deskripsi untuk customer", style="paragraph", max_length=MAX_DESCRIPTION,
                  placeholder="Opsional"),
        InputSpec("emoji", "Emoji tombol", parser="emoji", max_length=40, placeholder="Opsional, misal \U0001F4DD"),
    ),
    "price": (
        InputSpec("price_label", "Label field nominal", required=True, max_length=45,
                  placeholder="Contoh: Nominal yang mau dibayar"),
        InputSpec("currency_label", "Mata uang", max_length=20, placeholder="Kosong = mata uang toko"),
        InputSpec("fixed_price", "Harga tetap", parser="money", max_length=20, placeholder="Dipakai mode Harga tetap"),
        InputSpec("min_amount", "Nominal minimal", parser="money", max_length=20, placeholder="Opsional (mode nominal)"),
        InputSpec("max_amount", "Nominal maksimal", parser="money", max_length=20, placeholder="Opsional (mode nominal)"),
    ),
}
FORM_TITLE = {"text": "Produk Lainnya: Teks", "price": "Produk Lainnya: Harga"}


def form_defaults(form: str, values: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for spec in FORMS[form]:
        value = values.get(spec.key)
        if spec.parser == "money":
            out[spec.key] = money_text(value) if value else ""
        else:
            out[spec.key] = "" if value is None else str(value)
    return out


def defaults() -> dict:
    return {
        "enabled": 1, "label": DEFAULT_LABEL, "description": DEFAULT_DESCRIPTION, "emoji": None,
        "price_mode": "input", "fixed_price": 0.0, "min_amount": None, "max_amount": None,
        "price_label": DEFAULT_PRICE_LABEL, "currency_label": None,
    }


def values_from_config(cfg: Config) -> dict:
    return {
        "enabled": int(cfg.enabled), "label": cfg.label, "description": cfg.description, "emoji": cfg.emoji,
        "price_mode": cfg.price_mode, "fixed_price": cfg.fixed_price, "min_amount": cfg.min_amount,
        "max_amount": cfg.max_amount, "price_label": cfg.price_label, "currency_label": cfg.currency,
    }


def validate(v: dict) -> list[str]:
    problems = []
    label = (v.get("label") or "").strip()
    if not label:
        problems.append("Nama tombol/produk belum diisi.")
    elif len(label) > MAX_LABEL:
        problems.append(f"Nama maksimal {MAX_LABEL} karakter.")
    if v.get("emoji") and not is_valid_emoji(v["emoji"]):
        problems.append("Emoji gak valid.")
    if v.get("price_mode") == "fixed":
        if float(v.get("fixed_price") or 0) <= 0:
            problems.append("Harga tetap belum diisi (harus lebih dari 0).")
    else:
        if not (v.get("price_label") or "").strip():
            problems.append("Label field nominal belum diisi.")
        lo, hi = v.get("min_amount"), v.get("max_amount")
        if lo is not None and hi is not None and float(hi) < float(lo):
            problems.append("Nominal maksimal gak boleh lebih kecil dari minimal.")
    return problems


def apply_form(form: str, raw: dict) -> tuple[dict, list[str]]:
    """Jawaban modal -> (nilai valid, error). Field yang error gak ikut diterapin."""
    updates: dict = {}
    errors: list[str] = []
    text = lambda key: (raw.get(key) or "").strip()  # noqa: E731
    if form == "text":
        label = text("label")
        if not label:
            errors.append("Nama wajib diisi.")
        else:
            updates["label"] = label
        updates["description"] = text("description") or None
        emoji = text("emoji")
        if emoji and not is_valid_emoji(emoji):
            errors.append("Emoji gak valid -- pake emoji biasa atau custom emoji dari server ini.")
        else:
            updates["emoji"] = emoji or None
    else:  # price
        price_label = text("price_label")
        if not price_label:
            errors.append("Label field nominal wajib diisi.")
        else:
            updates["price_label"] = price_label[:45]
        updates["currency_label"] = text("currency_label")[:20] or None
        for key, label in (("fixed_price", "Harga tetap"), ("min_amount", "Nominal minimal"), ("max_amount", "Nominal maksimal")):
            value = text(key)
            if not value:
                updates[key] = 0.0 if key == "fixed_price" else None
                continue
            amount = parse_money(value)
            if amount is None:
                errors.append(f"{label}: '{value}' bukan angka yang valid.")
            else:
                updates[key] = amount
    return updates, errors


# ============================================================================
# PRODUK SISTEM
# ============================================================================

async def ensure_product(db: Database, values: dict, *, currency_fallback: str) -> tuple[int, int]:
    """Pastiin produk sistem ada dan nyamain namanya/harganya dengan
    pengaturan. Return (product_id, category_type_id). Dibikin ulang kalau
    kehapus (misal staff ngapus kategorinya lewat Builder)."""
    cfg = await load(db)
    currency = values.get("currency_label") or currency_fallback
    base_price = float(values.get("fixed_price") or 0) if values.get("price_mode") == "fixed" else 0.0
    name = (values.get("label") or DEFAULT_LABEL).strip()[:100]
    description = values.get("description")
    emoji = values.get("emoji")

    product = await products_q.get_product(db, cfg.product_id) if cfg.product_id else None
    if product is not None:
        await products_q.update_product(
            db, product["id"], name=name, description=description, emoji=emoji,
            currency_label=currency, base_price=base_price, visible=0,
            discount_type=None, discount_value=0,
        )
        return int(product["id"]), int(product["category_type_id"])

    category_id = await categories_q.create_category(db, SYSTEM_CATEGORY_NAME, "Dipakai fitur Produk Lainnya. Jangan dihapus.")
    await categories_q.set_category_enabled(db, category_id, False)   # nonaktif = gak muncul di /shop
    type_id = await category_types_q.create_category_type(db, category_id, SYSTEM_TYPE_NAME, "Field form Produk Lainnya")
    product_id = await products_q.create_product(
        db, type_id, name, description, "manual", "unlimited", 0, base_price, currency, None, emoji,
    )
    await products_q.update_product(db, product_id, visible=0)
    await custom_order_q.save_config(db, {"product_id": product_id})
    return product_id, type_id


async def save(db: Database, values: dict, *, currency_fallback: str) -> Config:
    """Simpen pengaturan + sinkronin produk sistemnya."""
    product_id, _ = await ensure_product(db, values, currency_fallback=currency_fallback)
    await custom_order_q.save_config(db, {
        "enabled": int(bool(values.get("enabled"))), "product_id": product_id,
        "label": (values.get("label") or DEFAULT_LABEL).strip(), "description": values.get("description"),
        "emoji": values.get("emoji"), "price_mode": values.get("price_mode", "input"),
        "fixed_price": float(values.get("fixed_price") or 0), "min_amount": values.get("min_amount"),
        "max_amount": values.get("max_amount"), "price_label": (values.get("price_label") or DEFAULT_PRICE_LABEL).strip(),
        "currency_label": values.get("currency_label"),
    })
    return await load(db)
