"""
Operasi database buat panel Catalog Builder (/builder). Tipis aja: baca
entitas jadi dict nilai draft, simpen draft (buat baru / update), daftar
anak tiap node, hapus (dengan penjaga riwayat order), geser posisi,
toggle aktif, dan atur stok.

Aturan nilai (kolom mana yang boleh ditulis, tipe, dst) ada di
bot.utils.builder_spec -- file ini cuma ngejalanin SQL-nya.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.database.core import Database
from bot.database.queries import categories as categories_q
from bot.database.queries import category_types as category_types_q
from bot.database.queries import fields as fields_q
from bot.database.queries import product_groups as product_groups_q
from bot.database.queries import products as products_q
from bot.utils import delete_guard
from bot.utils.builder_spec import (
    CATEGORY,
    EDITABLE,
    FIELD,
    GROUP,
    PRODUCT,
    TYPE,
    Draft,
)

_TABLE = {
    CATEGORY: "categories",
    TYPE: "category_types",
    GROUP: "product_groups",
    PRODUCT: "products",
    FIELD: "product_fields",
}
_NULLABLE_TEXT = ("description", "emoji", "image_url", "stock_label", "placeholder")
_BOOL_KEYS = ("enabled", "visible", "shared_stock", "required")


@dataclass
class Child:
    kind: str
    id: int
    label: str
    description: str
    emoji: str | None
    enabled: bool


@dataclass
class DeleteInfo:
    name: str
    impact: list[str]
    blocked: str | None = None   # alasan gak boleh dihapus (riwayat order/review)


# ============================================================================
# BACA
# ============================================================================

async def _row(db: Database, kind: str, entity_id: int):
    return await db.fetchone(f"SELECT * FROM {_TABLE[kind]} WHERE id = ?", (entity_id,))


async def load_values(db: Database, kind: str, entity_id: int) -> dict | None:
    """Entitas -> dict nilai draft (cuma kolom yang boleh diedit)."""
    row = await _row(db, kind, entity_id)
    if row is None:
        return None
    return {key: row[key] for key in EDITABLE[kind]}


async def group_row(db: Database, group_id: int | None) -> dict | None:
    if not group_id:
        return None
    row = await product_groups_q.get_product_group(db, group_id)
    return dict(row) if row else None


async def names_for(db: Database, values: dict) -> dict:
    """{(key, id): nama} buat nerjemahin id induk di daftar perubahan."""
    names: dict = {}
    if values.get("category_id"):
        row = await categories_q.get_category(db, values["category_id"])
        if row:
            names[("category_id", int(row["id"]))] = row["name"]
    if values.get("category_type_id"):
        row = await category_types_q.get_category_type(db, values["category_type_id"])
        if row:
            cat = await categories_q.get_category(db, row["category_id"])
            label = f"{cat['name']} \u203a {row['name']}" if cat else row["name"]
            names[("category_type_id", int(row["id"]))] = label
    if values.get("product_group_id"):
        row = await product_groups_q.get_product_group(db, values["product_group_id"])
        if row:
            names[("product_group_id", int(row["id"]))] = row["name"]
    return names


async def path_for(db: Database, values: dict, *, with_group: bool = True) -> str:
    """'Kategori > Category Type > Product Type' buat konteks di preview."""
    parts: list[str] = []
    ct_id = values.get("category_type_id")
    cat_id = values.get("category_id")
    if ct_id:
        ct = await category_types_q.get_category_type(db, ct_id)
        if ct:
            cat_id = ct["category_id"]
            cat = await categories_q.get_category(db, cat_id)
            if cat:
                parts.append(cat["name"])
            parts.append(ct["name"])
    elif cat_id:
        cat = await categories_q.get_category(db, cat_id)
        if cat:
            parts.append(cat["name"])
    if with_group and values.get("product_group_id"):
        g = await product_groups_q.get_product_group(db, values["product_group_id"])
        if g:
            parts.append(g["name"])
    return " \u203a ".join(parts)


async def default_currency(db: Database) -> str:
    from bot.utils.helpers import RuntimeSettings

    try:
        return await RuntimeSettings(db).default_currency()
    except Exception:  # noqa: BLE001
        return "IDR"


async def list_types_flat(db: Database):
    """Semua Category Type dengan nama kategorinya, buat picker lokasi."""
    return await db.fetchall(
        """
        SELECT t.id, t.name, t.emoji, t.enabled, c.name AS category_name
        FROM category_types t JOIN categories c ON c.id = t.category_id
        ORDER BY c.position ASC, c.id ASC, t.position ASC, t.id ASC
        """
    )


# ============================================================================
# ANAK TIAP NODE
# ============================================================================

async def _count(db: Database, query: str, *params) -> int:
    row = await db.fetchone(query, params)
    return int(row[0]) if row else 0


async def children(db: Database, kind: str | None, entity_id: int | None) -> list[Child]:
    """Anak yang bisa dibuka dari sebuah node. kind None = Home (kategori)."""
    out: list[Child] = []
    if kind is None:
        for c in await categories_q.list_categories(db):
            n_types = await _count(db, "SELECT COUNT(*) FROM category_types WHERE category_id = ?", c["id"])
            n_prod = await _count(
                db,
                "SELECT COUNT(*) FROM products WHERE category_type_id IN "
                "(SELECT id FROM category_types WHERE category_id = ?)",
                c["id"],
            )
            out.append(Child(CATEGORY, c["id"], c["name"], f"{n_types} tipe \u00B7 {n_prod} produk", c["emoji"], bool(c["enabled"])))
    elif kind == CATEGORY:
        for t in await category_types_q.list_category_types(db, category_id=entity_id):
            n_groups = await _count(db, "SELECT COUNT(*) FROM product_groups WHERE category_type_id = ?", t["id"])
            n_prod = await _count(db, "SELECT COUNT(*) FROM products WHERE category_type_id = ?", t["id"])
            extra = f"{n_groups} product type \u00B7 " if n_groups else ""
            out.append(Child(TYPE, t["id"], t["name"], f"{extra}{n_prod} produk", t["emoji"], bool(t["enabled"])))
    elif kind == TYPE:
        for g in await product_groups_q.list_product_groups(db, category_type_id=entity_id):
            n_prod = await _count(db, "SELECT COUNT(*) FROM products WHERE product_group_id = ?", g["id"])
            pool = " \u00B7 pool stok" if g["shared_stock"] else ""
            out.append(Child(GROUP, g["id"], g["name"], f"Product Type \u00B7 {n_prod} produk{pool}", g["emoji"], bool(g["enabled"])))
        for p in await products_q.list_products(db, category_type_id=entity_id, ungrouped=True):
            out.append(Child(PRODUCT, p["id"], p["name"], _product_desc(p), p["emoji"], bool(p["visible"])))
    elif kind == GROUP:
        for p in await products_q.list_products(db, product_group_id=entity_id):
            out.append(Child(PRODUCT, p["id"], p["name"], _product_desc(p), p["emoji"], bool(p["visible"])))
    return out


def _product_desc(p) -> str:
    from bot.utils.builder_spec import fmt_value

    return f"{fmt_value('base_price', p['base_price'], currency=p['currency_label'])}"


async def overview(db: Database) -> dict:
    return {
        "categories": await _count(db, "SELECT COUNT(*) FROM categories"),
        "types": await _count(db, "SELECT COUNT(*) FROM category_types"),
        "groups": await _count(db, "SELECT COUNT(*) FROM product_groups"),
        "products": await _count(db, "SELECT COUNT(*) FROM products"),
        "hidden": await _count(
            db,
            "SELECT COUNT(*) FROM products p WHERE p.visible = 0 OR p.category_type_id IN "
            "(SELECT id FROM category_types WHERE enabled = 0)",
        ),
        "out_of_stock": await _count(
            db,
            """
            SELECT COUNT(*) FROM products p
            LEFT JOIN product_groups g ON g.id = p.product_group_id
            WHERE (g.id IS NOT NULL AND g.shared_stock = 1 AND g.stock_quantity < p.stock_cost)
               OR ((g.id IS NULL OR g.shared_stock = 0) AND p.stock_type = 'manual'
                   AND p.stock_quantity < (CASE WHEN p.stock_mode = 'amount' THEN p.stock_cost ELSE 1 END))
            """,
        ),
    }


async def list_scope_fields(db: Database, *, group_id: int | None, category_type_id: int):
    if group_id:
        return await fields_q.list_group_fields(db, group_id)
    return await fields_q.list_fields(db, category_type_id)


# ============================================================================
# SIMPAN
# ============================================================================

def _clean(values: dict, keys) -> dict:
    out: dict = {}
    for key in keys:
        if key not in values:
            continue
        value = values[key]
        if isinstance(value, str):
            value = value.strip()
        if key in _NULLABLE_TEXT and value in ("", None):
            value = None
        if key in _BOOL_KEYS:
            value = int(bool(value))
        if key in ("product_group_id", "category_id", "category_type_id") and value in ("", 0):
            value = None
        out[key] = value
    return out


async def _update(db: Database, kind: str, entity_id: int, values: dict) -> None:
    cleaned = _clean(values, EDITABLE[kind])
    if not cleaned:
        return
    sets = ", ".join(f"{k} = ?" for k in cleaned)
    await db.execute(
        f"UPDATE {_TABLE[kind]} SET {sets} WHERE id = ?", (*cleaned.values(), entity_id)
    )


async def save(db: Database, draft: Draft) -> int:
    """Simpen draft: bikin baru atau update yang ada. Return id entitasnya.
    Nilai udah divalidasi caller (builder_spec.validate)."""
    v = _clean(draft.values, EDITABLE[draft.kind])
    kind = draft.kind

    if draft.entity_id is not None:
        await _update(db, kind, draft.entity_id, draft.values)
        return draft.entity_id

    if kind == CATEGORY:
        new_id = await categories_q.create_category(db, v["name"], v.get("description"), v.get("emoji"))
    elif kind == TYPE:
        new_id = await category_types_q.create_category_type(
            db, v["category_id"], v["name"], v.get("description"), v.get("emoji")
        )
    elif kind == GROUP:
        new_id = await product_groups_q.create_product_group(
            db, v["category_type_id"], v["name"], v.get("description"), v.get("emoji")
        )
    elif kind == PRODUCT:
        new_id = await products_q.create_product(
            db, v["category_type_id"], v["name"], v.get("description"), v["product_type"],
            v["stock_type"], int(v.get("stock_quantity") or 0), float(v["base_price"]),
            v["currency_label"], v.get("image_url"), v.get("emoji"),
        )
    elif kind == FIELD:
        new_id = await fields_q.create_field(
            db, v["category_type_id"], v["label"], v["field_type"], bool(v.get("required", 1)),
            v.get("placeholder"), int(v.get("min_length") or 0), int(v.get("max_length") or 100),
            v.get("validation", "none"), product_group_id=v.get("product_group_id"),
        )
    else:  # pragma: no cover
        raise ValueError(kind)
    # Create cuma nerima sebagian kolom -- sisanya (status, diskon, stok bersama,
    # dst) ditulis lewat update biar draft tersimpan UTUH.
    await _update(db, kind, new_id, draft.values)
    return new_id


# ============================================================================
# HAPUS
# ============================================================================

async def delete_info(db: Database, kind: str, entity_id: int) -> DeleteInfo | None:
    row = await _row(db, kind, entity_id)
    if row is None:
        return None
    name = row["label"] if kind == FIELD else row["name"]
    impact: list[str] = []
    blocked = None
    if kind == CATEGORY:
        n_t = await _count(db, "SELECT COUNT(*) FROM category_types WHERE category_id = ?", entity_id)
        n_g = await _count(db, "SELECT COUNT(*) FROM product_groups WHERE category_type_id IN (SELECT id FROM category_types WHERE category_id = ?)", entity_id)
        n_p = await _count(db, "SELECT COUNT(*) FROM products WHERE category_type_id IN (SELECT id FROM category_types WHERE category_id = ?)", entity_id)
        impact = [f"{n_t} Category Type", f"{n_g} Product Type", f"{n_p} produk"]
        blocked = await delete_guard.blocked_message(
            db, what=f"Kategori **{name}**", hide_hint="Nonaktifin aja biar disembunyiin dari /shop (riwayat tetap aman).",
            category_id=entity_id,
        )
    elif kind == TYPE:
        n_g = await _count(db, "SELECT COUNT(*) FROM product_groups WHERE category_type_id = ?", entity_id)
        n_p = await _count(db, "SELECT COUNT(*) FROM products WHERE category_type_id = ?", entity_id)
        n_f = await _count(db, "SELECT COUNT(*) FROM product_fields WHERE category_type_id = ?", entity_id)
        impact = [f"{n_g} Product Type", f"{n_p} produk", f"{n_f} field checkout"]
        blocked = await delete_guard.blocked_message(
            db, what=f"Category Type **{name}**", hide_hint="Nonaktifin aja biar disembunyiin dari /shop (riwayat tetap aman).",
            category_type_id=entity_id,
        )
    elif kind == GROUP:
        n_p = await _count(db, "SELECT COUNT(*) FROM products WHERE product_group_id = ?", entity_id)
        n_f = await _count(db, "SELECT COUNT(*) FROM product_fields WHERE product_group_id = ?", entity_id)
        impact = [f"{n_p} produk TIDAK ikut kehapus (dilepas dari Product Type ini)", f"{n_f} field tambahan ikut kehapus"]
    elif kind == PRODUCT:
        n_c = await _count(db, "SELECT COUNT(*) FROM cart_items WHERE product_id = ?", entity_id)
        impact = [f"{n_c} keranjang customer berisi produk ini akan dikosongin dari produk ini"] if n_c else []
        blocked = await delete_guard.blocked_message(
            db, what=f"Produk **{name}**", hide_hint="Sembunyiin aja biar gak muncul di /shop (riwayat tetap aman).",
            product_id=entity_id,
        )
    return DeleteInfo(name=name, impact=impact, blocked=blocked)


async def delete(db: Database, kind: str, entity_id: int) -> tuple[bool, str | None]:
    """Hapus permanen. (False, pesan) kalau ditolak database."""
    try:
        if kind == CATEGORY:
            await categories_q.delete_category(db, entity_id)
        elif kind == TYPE:
            await category_types_q.delete_category_type(db, entity_id)
        elif kind == GROUP:
            await product_groups_q.delete_product_group(db, entity_id)
        elif kind == PRODUCT:
            await products_q.delete_product(db, entity_id)
        elif kind == FIELD:
            await fields_q.delete_field(db, entity_id)
    except Exception as exc:  # noqa: BLE001
        if delete_guard.is_fk_error(exc):
            return False, delete_guard.FALLBACK_MESSAGE
        raise
    return True, None


# ============================================================================
# TOGGLE, POSISI, STOK
# ============================================================================

_ENABLE_COLUMN = {CATEGORY: "enabled", TYPE: "enabled", GROUP: "enabled", PRODUCT: "visible"}


async def toggle(db: Database, kind: str, entity_id: int) -> bool | None:
    """Balik status aktif/tampil. Return status baru (None kalau gak ada)."""
    column = _ENABLE_COLUMN[kind]
    row = await _row(db, kind, entity_id)
    if row is None:
        return None
    new_value = 0 if row[column] else 1
    await db.execute(f"UPDATE {_TABLE[kind]} SET {column} = ? WHERE id = ?", (new_value, entity_id))
    return bool(new_value)


async def move(db: Database, kind: str, entity_id: int, direction: int) -> bool:
    """Geser satu langkah (-1 naik, +1 turun) di antara saudaranya. Posisi
    saudara dinormalisasi jadi 0..n-1 biar urutannya pasti. False kalau
    udah di ujung."""
    row = await _row(db, kind, entity_id)
    if row is None:
        return False
    if kind == CATEGORY:
        rows = await db.fetchall("SELECT id FROM categories ORDER BY position ASC, id ASC")
    elif kind == TYPE:
        rows = await db.fetchall(
            "SELECT id FROM category_types WHERE category_id = ? ORDER BY position ASC, id ASC", (row["category_id"],)
        )
    elif kind == GROUP:
        rows = await db.fetchall(
            "SELECT id FROM product_groups WHERE category_type_id = ? ORDER BY position ASC, id ASC",
            (row["category_type_id"],),
        )
    elif kind == PRODUCT:
        rows = await db.fetchall(
            "SELECT id FROM products WHERE category_type_id = ? AND product_group_id IS ? "
            "ORDER BY position ASC, id ASC",
            (row["category_type_id"], row["product_group_id"]),
        )
    else:
        return False
    ids = [r["id"] for r in rows]
    index = ids.index(entity_id)
    target = index + direction
    if target < 0 or target >= len(ids):
        return False
    ids[index], ids[target] = ids[target], ids[index]
    for position, sibling_id in enumerate(ids):
        await db.execute(f"UPDATE {_TABLE[kind]} SET position = ? WHERE id = ?", (position, sibling_id))
    return True


async def apply_stock(
    db: Database, kind: str, entity_id: int, action: str, amount: int
) -> tuple[bool, str]:
    """Atur stok langsung (restock cepat). action: set | add | subtract.
    Produk yang narik dari pool bersama gak punya stok sendiri -- atur
    lewat Product Type-nya."""
    if kind == PRODUCT:
        row = await products_q.get_product(db, entity_id)
        if row is None:
            return False, "Produk gak ketemu."
        group = await product_groups_q.get_product_group(db, row["product_group_id"]) if row["product_group_id"] else None
        if group and group["shared_stock"]:
            return False, f"Produk ini narik dari pool bersama **{group['name']}** -- atur stoknya lewat Product Type itu."
        if row["stock_type"] != "manual":
            return False, "Produk ini stoknya unlimited. Ubah tipe stok ke manual dulu lewat Edit."
        current, unit = int(row["stock_quantity"]), row["stock_label"]
        name = row["name"]
        setter = products_q.set_stock
    elif kind == GROUP:
        row = await product_groups_q.get_product_group(db, entity_id)
        if row is None:
            return False, "Product Type gak ketemu."
        if not row["shared_stock"]:
            return False, "Product Type ini gak pake pool stok bersama. Nyalain dulu lewat Edit."
        current, unit = int(row["stock_quantity"]), row["stock_label"]
        name = row["name"]
        setter = product_groups_q.set_stock
    else:
        return False, "Entitas ini gak punya stok."
    new_value = {"set": amount, "add": current + amount, "subtract": max(0, current - amount)}[action]
    await setter(db, entity_id, new_value)
    suffix = f" {unit}" if unit else ""
    return True, f"Stok **{name}**: {current:,}{suffix} \u2192 **{new_value:,}{suffix}**."


def duplicate_values(values: dict) -> dict:
    copy = dict(values)
    name = f"{copy.get('name') or ''} (Copy)".strip()
    copy["name"] = name[:100]
    return copy
