"""
Logika murni (TANPA Discord, TANPA database) buat panel Catalog Builder
(/builder): definisi tiap entitas, form modal, parser input, validasi,
diff "sebelum -> sesudah", dan teks preview.

Dipisah dari UI (bot.ui.builder) dan akses DB (bot.utils.builder_store)
biar semua aturannya bisa dites sendiri.

Lima entitas ("kind"):
  category | type (Category Type) | group (Product Type) | product | field

Semua perubahan lewat DRAFT: nilai-nilai yang lagi diedit disimpen di
memori (bukan di DB) sampai staff nekan Simpan, jadi preview dan daftar
perubahan bisa ditampilin dulu, dan salah ketik gak langsung ngubah toko.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from bot.utils.helpers import calculate_final_price, discount_label, format_price
from bot.utils.stock import format_stock, resolve_source
from bot.utils.validators import is_valid_emoji

CATEGORY, TYPE, GROUP, PRODUCT, FIELD = "category", "type", "group", "product", "field"
KINDS = (CATEGORY, TYPE, GROUP, PRODUCT, FIELD)

KIND_LABEL = {
    CATEGORY: "Kategori",
    TYPE: "Category Type",
    GROUP: "Product Type",
    PRODUCT: "Produk",
    FIELD: "Field Checkout",
}

PRODUCT_TYPES = ("manual", "automatic", "digital", "service")
STOCK_TYPES = ("unlimited", "manual")
STOCK_MODES = ("unit", "amount")
DISCOUNT_TYPES = (None, "percent", "flat")
FIELD_TYPES = ("username", "userid", "login", "email", "password", "serverid", "gameid", "custom")
VALIDATIONS = ("none", "numeric", "alpha", "alphanumeric", "email")

DISCOUNT_WORD = {None: "tanpa", "percent": "persen", "flat": "nominal"}

# Kolom yang boleh diedit per entitas (whitelist -- builder_store cuma nulis ini).
EDITABLE = {
    CATEGORY: ("name", "description", "emoji", "enabled"),
    TYPE: ("category_id", "name", "description", "emoji", "enabled"),
    GROUP: (
        "category_type_id", "name", "description", "emoji", "enabled",
        "shared_stock", "stock_mode", "stock_quantity", "stock_label",
    ),
    PRODUCT: (
        "category_type_id", "product_group_id", "name", "description", "image_url", "emoji",
        "product_type", "stock_type", "stock_mode", "stock_quantity", "stock_cost", "stock_label",
        "base_price", "currency_label", "discount_type", "discount_value", "visible",
    ),
    FIELD: (
        "category_type_id", "product_group_id", "label", "field_type", "required",
        "placeholder", "min_length", "max_length", "validation",
    ),
}

KEY_LABEL = {
    "name": "Nama", "description": "Deskripsi", "emoji": "Emoji", "image_url": "Gambar",
    "enabled": "Status", "visible": "Status", "category_id": "Kategori",
    "category_type_id": "Category Type", "product_group_id": "Product Type",
    "product_type": "Pengiriman", "stock_type": "Tipe stok", "stock_mode": "Mode stok",
    "stock_quantity": "Stok", "stock_cost": "Stok per qty", "stock_label": "Satuan stok",
    "shared_stock": "Pool bersama", "base_price": "Harga", "currency_label": "Mata uang",
    "discount_type": "Tipe diskon", "discount_value": "Nilai diskon", "label": "Label",
    "field_type": "Jenis", "required": "Wajib", "placeholder": "Placeholder",
    "min_length": "Min karakter", "max_length": "Maks karakter", "validation": "Validasi",
}

MAX_NAME = 100
MAX_SHORT_DESC = 100     # batas deskripsi opsi Select Discord
MAX_PRODUCT_DESC = 1000
MAX_URL = 500
MAX_LABEL_FIELD = 45     # batas label TextInput Discord


# ============================================================================
# PARSER INPUT
# ============================================================================

def parse_money(text: str | None) -> float | None:
    """'15000', '15.000', 'Rp 15.000', '12,50', '1.000.000' -> float.
    Titik/koma yang diikuti persis 3 digit dianggep pemisah ribuan
    ('15.000' = 15000), selain itu desimal ('12,50' = 12.5). Kalau ada
    titik DAN koma, yang paling kanan itu desimalnya. None = gak valid
    atau negatif."""
    raw = (text or "").strip()
    if not raw or raw.startswith("-"):
        return None
    t = re.sub(r"[^\d.,]", "", raw)
    if not t or not re.search(r"\d", t):
        return None
    if "," in t and "." in t:
        if t.rfind(",") > t.rfind("."):
            t = t.replace(".", "").replace(",", ".")
        else:
            t = t.replace(",", "")
    elif "," in t or "." in t:
        sep = "," if "," in t else "."
        parts = t.split(sep)
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            t = t.replace(sep, "")
        else:
            t = t.replace(sep, ".")
    try:
        value = float(t)
    except ValueError:
        return None
    return value if value >= 0 else None


def parse_int(text: str | None) -> int | None:
    """'1500', '10.000', '10,000' -> int. None kalau bukan bilangan bulat >= 0."""
    raw = (text or "").strip()
    if not raw or raw.startswith("-"):
        return None
    t = re.sub(r"[.,\s]", "", raw)
    return int(t) if t.isdigit() else None


def valid_url(text: str | None) -> bool:
    value = (text or "").strip()
    if not value or len(value) > 2000 or re.search(r"\s", value):
        return False
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and "." in parsed.netloc


def cycle(options: tuple, current):
    """Nilai berikutnya di `options` (muter balik ke awal)."""
    index = options.index(current) if current in options else -1
    return options[(index + 1) % len(options)]


def money_text(value) -> str:
    """Angka -> teks buat nilai default input (tanpa .0 yang gak perlu)."""
    if value is None:
        return ""
    value = float(value)
    return str(int(value)) if value == int(value) else f"{value:g}"


# ============================================================================
# FORM MODAL
# ============================================================================

@dataclass(frozen=True)
class InputSpec:
    key: str
    label: str
    parser: str = "text"        # text | money | int | url | emoji
    style: str = "short"        # short | paragraph
    required: bool = False
    max_length: int = 100
    placeholder: str | None = None


_INFO = (
    InputSpec("name", "Nama", required=True, max_length=MAX_NAME),
    InputSpec("description", "Deskripsi", style="paragraph", max_length=MAX_SHORT_DESC,
              placeholder="Opsional"),
    InputSpec("emoji", "Emoji", parser="emoji", max_length=40,
              placeholder="Opsional, misal \U0001F3AE atau emoji custom server"),
)

FORMS: dict[tuple[str, str], tuple[InputSpec, ...]] = {
    (CATEGORY, "info"): _INFO,
    (TYPE, "info"): _INFO,
    (GROUP, "info"): _INFO,
    (GROUP, "stock"): (
        InputSpec("stock_quantity", "Isi pool stok", parser="int", required=True, max_length=12,
                  placeholder="Contoh: 10000"),
        InputSpec("stock_label", "Satuan stok", max_length=20, placeholder="Opsional, misal Robux"),
    ),
    (PRODUCT, "info"): (
        InputSpec("name", "Nama produk", required=True, max_length=MAX_NAME),
        InputSpec("description", "Deskripsi", style="paragraph", max_length=MAX_PRODUCT_DESC,
                  placeholder="Opsional"),
        InputSpec("image_url", "URL gambar (banner/thumbnail)", parser="url", max_length=MAX_URL,
                  placeholder="https://... (PNG/JPG/WebP, opsional)"),
        InputSpec("emoji", "Emoji", parser="emoji", max_length=40, placeholder="Opsional"),
    ),
    (PRODUCT, "price"): (
        InputSpec("base_price", "Harga dasar (sebelum diskon)", parser="money", required=True,
                  max_length=20, placeholder="Contoh: 15000 atau 15.000"),
        InputSpec("currency_label", "Mata uang", required=True, max_length=20,
                  placeholder="Contoh: IDR, USD, Robux"),
        InputSpec("discount_value", "Nilai diskon", parser="money", max_length=20,
                  placeholder="Angka persen atau nominal. Tipe diskon diatur lewat tombol"),
    ),
    (PRODUCT, "stock"): (
        InputSpec("stock_quantity", "Stok sekarang", parser="int", required=True, max_length=12,
                  placeholder="Cuma kepake kalau stok manual"),
        InputSpec("stock_cost", "Stok yang kepake per 1 qty", parser="int", required=True,
                  max_length=8, placeholder="Mode jumlah: misal 800 buat paket 800 Robux"),
        InputSpec("stock_label", "Satuan stok", max_length=20, placeholder="Opsional, misal Robux"),
    ),
    (FIELD, "info"): (
        InputSpec("label", "Label (yang dilihat customer)", required=True, max_length=MAX_LABEL_FIELD),
        InputSpec("placeholder", "Placeholder", max_length=100, placeholder="Opsional"),
        InputSpec("min_length", "Karakter minimal", parser="int", max_length=4, placeholder="0"),
        InputSpec("max_length", "Karakter maksimal", parser="int", required=True, max_length=4,
                  placeholder="1 - 4000"),
    ),
}

FORM_TITLE = {
    "info": "Info Dasar", "price": "Harga & Diskon", "stock": "Stok",
}


def form_defaults(kind: str, form: str, values: dict) -> dict[str, str]:
    """Nilai awal tiap input modal (dari draft) dalam bentuk teks."""
    out: dict[str, str] = {}
    for spec in FORMS[(kind, form)]:
        value = values.get(spec.key)
        if spec.parser == "money":
            out[spec.key] = money_text(value) if value not in (None, 0, 0.0) or spec.required else ""
        elif spec.parser == "int":
            out[spec.key] = "" if value is None else str(int(value))
        else:
            out[spec.key] = "" if value is None else str(value)
    return out


def apply_form(kind: str, form: str, raw: dict[str, str]) -> tuple[dict, list[str]]:
    """Parse jawaban modal -> (nilai valid yang mau diterapin ke draft,
    daftar pesan error). Field yang error TIDAK ikut diterapin (nilai
    lamanya dipertahankan); sisanya tetep masuk."""
    updates: dict = {}
    errors: list[str] = []
    for spec in FORMS[(kind, form)]:
        text = (raw.get(spec.key) or "").strip()
        if not text:
            if spec.required:
                errors.append(f"{spec.label} wajib diisi.")
            else:
                updates[spec.key] = None if spec.parser in ("text", "url", "emoji") else 0
            continue
        if spec.parser == "money":
            value = parse_money(text)
            if value is None:
                errors.append(f"{spec.label}: '{text}' bukan angka yang valid.")
                continue
            updates[spec.key] = value
        elif spec.parser == "int":
            value = parse_int(text)
            if value is None:
                errors.append(f"{spec.label}: '{text}' harus bilangan bulat (0 atau lebih).")
                continue
            updates[spec.key] = value
        elif spec.parser == "url":
            if not valid_url(text):
                errors.append(f"{spec.label}: harus link http(s) yang valid (tanpa spasi).")
                continue
            updates[spec.key] = text
        elif spec.parser == "emoji":
            if not is_valid_emoji(text):
                errors.append("Emoji gak valid -- pake emoji biasa atau custom emoji dari server ini.")
                continue
            updates[spec.key] = text
        else:
            updates[spec.key] = text
    return updates, errors


# ============================================================================
# DRAFT
# ============================================================================

@dataclass
class Draft:
    kind: str
    entity_id: int | None
    values: dict
    original: dict | None = None
    note: str | None = None          # misal "Duplikat dari #12"
    extra: dict = field(default_factory=dict)

    @property
    def is_new(self) -> bool:
        return self.entity_id is None

    def changed_keys(self) -> list[str]:
        if self.original is None:
            return []
        return [k for k in EDITABLE[self.kind] if _norm(self.values.get(k)) != _norm(self.original.get(k))]


def _norm(value):
    if value in ("", None):
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, float) and value == int(value):
        return int(value)
    return value


def new_values(kind: str, *, parent: dict | None = None, default_currency: str = "IDR") -> dict:
    """Nilai awal draft baru. `parent` = id induk yang lagi dibuka
    (category_id / category_type_id / product_group_id)."""
    parent = parent or {}
    if kind == CATEGORY:
        return {"name": None, "description": None, "emoji": None, "enabled": 1}
    if kind == TYPE:
        return {"category_id": parent.get("category_id"), "name": None, "description": None,
                "emoji": None, "enabled": 1}
    if kind == GROUP:
        return {"category_type_id": parent.get("category_type_id"), "name": None, "description": None,
                "emoji": None, "enabled": 1, "shared_stock": 0, "stock_mode": "unit",
                "stock_quantity": 0, "stock_label": None}
    if kind == PRODUCT:
        return {
            "category_type_id": parent.get("category_type_id"),
            "product_group_id": parent.get("product_group_id"),
            "name": None, "description": None, "image_url": None, "emoji": None,
            "product_type": "manual", "stock_type": "unlimited", "stock_mode": "unit",
            "stock_quantity": 0, "stock_cost": 1, "stock_label": None,
            "base_price": None, "currency_label": default_currency,
            "discount_type": None, "discount_value": 0, "visible": 1,
        }
    if kind == FIELD:
        return {"category_type_id": parent.get("category_type_id"),
                "product_group_id": parent.get("product_group_id"),
                "label": None, "field_type": "custom", "required": 1, "placeholder": None,
                "min_length": 0, "max_length": 100, "validation": "none"}
    raise ValueError(kind)


# ============================================================================
# VALIDASI
# ============================================================================

def validate(kind: str, v: dict, *, group: dict | None = None, group_type_id: int | None = None) -> list[str]:
    """Daftar masalah yang ngeblok Simpan (kosong = boleh disimpen).
    `group` = row Product Type yang dipilih (buat cek stok bersama),
    `group_type_id` = Category Type milik group itu."""
    problems: list[str] = []

    def need_name(key="name", label="Nama"):
        value = (v.get(key) or "").strip()
        if not value:
            problems.append(f"{label} belum diisi.")
        elif len(value) > (MAX_LABEL_FIELD if key == "label" else MAX_NAME):
            problems.append(f"{label} kepanjangan.")

    if kind == CATEGORY:
        need_name()
    elif kind == TYPE:
        need_name()
        if not v.get("category_id"):
            problems.append("Kategori induk belum dipilih.")
    elif kind == GROUP:
        need_name()
        if not v.get("category_type_id"):
            problems.append("Category Type induk belum dipilih.")
        if v.get("shared_stock") and int(v.get("stock_quantity") or 0) < 0:
            problems.append("Isi pool stok gak boleh negatif.")
    elif kind == PRODUCT:
        need_name()
        if not v.get("category_type_id"):
            problems.append("Category Type belum dipilih (buka Lokasi).")
        elif v.get("product_group_id") and group_type_id is not None \
                and int(group_type_id) != int(v["category_type_id"]):
            problems.append("Product Type bukan bagian dari Category Type yang dipilih.")
        price = v.get("base_price")
        if price is None:
            problems.append("Harga belum diisi.")
        elif float(price) < 0:
            problems.append("Harga gak boleh negatif.")
        if not (v.get("currency_label") or "").strip():
            problems.append("Mata uang belum diisi.")
        dtype, dvalue = v.get("discount_type"), float(v.get("discount_value") or 0)
        if dtype == "percent" and not (0 < dvalue <= 100):
            problems.append("Diskon persen harus lebih dari 0 dan maksimal 100.")
        if dtype == "flat":
            if dvalue <= 0:
                problems.append("Diskon nominal harus lebih dari 0.")
            elif price is not None and dvalue > float(price):
                problems.append("Diskon nominal gak boleh lebih besar dari harga.")
        pooled = bool(group and group.get("shared_stock"))
        if v.get("stock_mode") == "amount" and v.get("stock_type") != "manual" and not pooled:
            problems.append("Mode stok jumlah butuh tipe stok manual (atau Product Type dengan pool bersama).")
        if int(v.get("stock_cost") or 0) < 1:
            problems.append("Stok per qty minimal 1.")
        image = (v.get("image_url") or "").strip()
        if image and not valid_url(image):
            problems.append("URL gambar gak valid.")
    elif kind == FIELD:
        need_name("label", "Label")
        if not v.get("category_type_id"):
            problems.append("Category Type induk gak ketemu.")
        min_len, max_len = int(v.get("min_length") or 0), int(v.get("max_length") or 0)
        if max_len < 1 or max_len > 4000:
            problems.append("Karakter maksimal harus 1-4000.")
        elif min_len > max_len:
            problems.append("Karakter minimal gak boleh lebih besar dari maksimal.")
    return problems


# ============================================================================
# TAMPILAN NILAI, DIFF, PREVIEW
# ============================================================================

def _yesno(value) -> str:
    return "ya" if value else "tidak"


def fmt_value(key: str, value, names: dict | None = None, currency: str | None = None) -> str:
    """Nilai -> teks pendek buat daftar perubahan. `names` = {(key, id): nama}
    buat nerjemahin id induk jadi nama."""
    names = names or {}
    if key in ("category_id", "category_type_id", "product_group_id"):
        if value in (None, ""):
            return "-" if key != "product_group_id" else "(tanpa Product Type)"
        return names.get((key, int(value)), f"#{value}")
    if key in ("enabled", "visible"):
        return "aktif" if value else "nonaktif"
    if key in ("shared_stock", "required"):
        return _yesno(value)
    if key == "discount_type":
        return DISCOUNT_WORD.get(value, str(value))
    if key in ("base_price",):
        return "-" if value is None else format_price(float(value), currency or "")
    if key == "discount_value":
        return money_text(value) if value else "0"
    if key in ("stock_quantity", "stock_cost", "min_length", "max_length"):
        return "-" if value is None else f"{int(value):,}"
    if value in (None, ""):
        return "-"
    text = str(value).replace("\n", " ")
    return text if len(text) <= 60 else text[:57] + "..."


def diff(draft: Draft, names: dict | None = None) -> list[str]:
    """Baris "- Harga: 15.000 -> 12.000" buat tiap kolom yang beda dari
    aslinya (draft edit). Draft baru = list kosong."""
    if draft.original is None:
        return []
    currency = draft.values.get("currency_label") or draft.original.get("currency_label")
    lines = []
    for key in draft.changed_keys():
        old = fmt_value(key, draft.original.get(key), names, currency)
        new = fmt_value(key, draft.values.get(key), names, currency)
        lines.append(f"\u2022 **{KEY_LABEL.get(key, key)}**: `{old}` \u2192 `{new}`")
    return lines


def _title(values: dict, key: str = "name") -> str:
    emoji = values.get("emoji")
    name = (values.get(key) or "").strip() or "*(belum diberi nama)*"
    return f"{emoji} {name}" if emoji else name


def price_line(v: dict) -> str:
    """Harga persis kayak yang dilihat customer (coret + diskon)."""
    if v.get("base_price") is None:
        return "*(belum diisi)*"
    currency = v.get("currency_label") or ""
    base = float(v["base_price"])
    final = calculate_final_price(base, v.get("discount_type"), v.get("discount_value") or 0)
    label = discount_label(v.get("discount_type"), v.get("discount_value") or 0)
    if label and final != base:
        return f"~~{format_price(base, currency)}~~ **{format_price(final, currency)}** ({label})"
    return f"**{format_price(final, currency)}**"


def stock_line(v: dict, group: dict | None) -> str:
    row = dict(v)
    row.setdefault("id", 0)
    row["id"] = row.get("id") or 0
    row["stock_cost"] = row.get("stock_cost") or 1
    group_row = None
    if group:
        group_row = dict(group)
    source = resolve_source(row, group_row)
    text = format_stock(source, with_cost=True)
    if group_row and group_row.get("shared_stock"):
        text += f" \u2014 pool **{group_row.get('name')}**"
    return text


def describe(kind: str, v: dict, *, names: dict | None = None, group: dict | None = None,
             path: str | None = None) -> str:
    """Preview teks sebuah entitas (draft atau data tersimpan). Dipake
    panel buat nunjukin 'ini lho yang bakal tersimpan'."""
    names = names or {}
    lines: list[str] = []
    if kind in (CATEGORY, TYPE, GROUP):
        lines.append(f"### {_title(v)}")
        lines.append((v.get("description") or "*Belum ada deskripsi.*"))
        status = "\u2705 Aktif" if v.get("enabled", 1) else "\u26D4 Nonaktif (disembunyiin dari /shop)"
        lines.append(f"**Status** {status}")
        if kind == TYPE:
            lines.append(f"**Kategori** {fmt_value('category_id', v.get('category_id'), names)}")
        if kind == GROUP:
            lines.append(f"**Category Type** {fmt_value('category_type_id', v.get('category_type_id'), names)}")
            if v.get("shared_stock"):
                unit = f" {v['stock_label']}" if v.get("stock_label") else ""
                mode = "jumlah" if v.get("stock_mode") == "amount" else "satuan"
                lines.append(f"**Stok bersama** ({mode}) {int(v.get('stock_quantity') or 0):,}{unit}")
            else:
                lines.append("**Stok bersama** tidak dipakai (stok per produk)")
    elif kind == PRODUCT:
        lines.append(f"### {_title(v)}")
        lines.append((v.get("description") or "*Belum ada deskripsi.*")[:300])
        lines.append(f"**Harga** {price_line(v)}")
        lines.append(f"**Stok** {stock_line(v, group)}")
        meta = [f"Pengiriman: {v.get('product_type')}"]
        meta.append("\u2705 Tampil" if v.get("visible", 1) else "\u26D4 Disembunyiin")
        lines.append(" \u00B7 ".join(meta))
        if path:
            lines.append(f"-# {path}")
    elif kind == FIELD:
        label = (v.get("label") or "").strip() or "*(belum diberi label)*"
        req = "wajib" if v.get("required", 1) else "opsional"
        lines.append(f"### \U0001F4DD {label}")
        lines.append(f"Input teks \u00B7 {req} \u00B7 {int(v.get('min_length') or 0)}-{int(v.get('max_length') or 0)} karakter")
        lines.append(f"Jenis: **{v.get('field_type')}** \u00B7 Validasi: **{v.get('validation')}**")
        if v.get("placeholder"):
            lines.append(f"Placeholder: *{v['placeholder']}*")
        if path:
            lines.append(f"-# {path}")
    return "\n".join(lines)
