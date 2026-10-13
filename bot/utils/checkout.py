"""
Logic keranjang & checkout yang dipake bareng sama UI (bot.ui.views), task
background (bot.cogs.tasks), dan aksi order (bot.utils.order_actions):

  * build_lines()      -- ubah isi keranjang jadi CartLine yang udah divalidasi
                          (produk masih ada/aktif, stok cukup, harga terhitung).
  * add_to_cart() / try_set_quantity() -- ubah keranjang dengan validasi stok.
  * collect_fields()   -- gabungin dynamic checkout field dari semua Category
                          Type yang ada di keranjang.
  * reserve_lines() / release_reserved() / release_order_stock() -- potong
                          dan balikin stok secara atomik.

Aturan penting:
  * SEMUA item di satu keranjang harus pake mata uang yang sama (label
    currency yang sama) -- gak bisa dijumlahin lintas mata uang.
  * Stok yang dipake dihitung per POOL, jadi dua produk yang narik dari
    pool bersama yang sama saling ngurangin jatah (lihat bot.utils.stock).
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.core.logger import logger
from bot.database.core import Database
from bot.database.queries import cart as cart_q
from bot.database.queries import categories as categories_q
from bot.database.queries import category_types as category_types_q
from bot.database.queries import fields as fields_q
from bot.database.queries import orders as orders_q
from bot.database.queries import product_groups as product_groups_q
from bot.database.queries import products as products_q
from bot.utils.helpers import calculate_final_price
from bot.utils.stock import (
    SOURCE_GROUP,
    SOURCE_NONE,
    SOURCE_PRODUCT,
    StockSource,
    format_stock,
    resolve_source,
)

MAX_CART_LINES = 15      # jumlah produk BEDA maksimal di satu keranjang
MAX_LINE_QTY = 99        # qty maksimal per produk
MODAL_LABEL_MAX = 45     # batas label TextInput Discord


@dataclass(frozen=True)
class CheckoutRequest:
    """Apa yang mau di-checkout: daftar (product_id, quantity). `from_cart`
    True = berasal dari keranjang (baris yang dibeli dibersihin dari
    keranjang begitu order jadi); False = "Beli Sekarang" / /buy langsung
    (keranjang gak disentuh)."""
    lines: tuple[tuple[int, int], ...]
    from_cart: bool = False


@dataclass
class CartLine:
    product: object                  # row products
    group: object | None             # row product_groups (atau None)
    quantity: int
    unit_price: float
    line_total: float
    source: StockSource
    problem: str | None = None       # kalau diisi: line ini gak bisa di-checkout

    @property
    def product_id(self) -> int:
        return int(self.product["id"])

    @property
    def name(self) -> str:
        return self.product["name"]

    @property
    def currency(self) -> str:
        return self.product["currency_label"]

    @property
    def stock_needed(self) -> int:
        return self.source.needed(self.quantity)

    def max_quantity(self) -> int:
        """Qty maksimal buat line ini (sisa pool, dibatesin MAX_LINE_QTY)."""
        limit = self.source.max_quantity()
        return MAX_LINE_QTY if limit is None else max(0, min(MAX_LINE_QTY, limit))


# ============================================================================
# MEMBANGUN & MEMVALIDASI LINE
# ============================================================================

async def _product_unavailable_reason(db: Database, product, group) -> str | None:
    """Alasan produk gak bisa dibeli sekarang (None = bisa). Cek rantai
    penuh: produk visible, Category Type & Category aktif, Product Type
    (kalau ada) aktif."""
    if not product["visible"]:
        return "Produk ini lagi gak tersedia."
    category_type = await category_types_q.get_category_type(db, product["category_type_id"])
    if category_type is None or not category_type["enabled"]:
        return "Produk ini lagi gak tersedia."
    category = await categories_q.get_category(db, category_type["category_id"])
    if category is None or not category["enabled"]:
        return "Produk ini lagi gak tersedia."
    if group is not None and not group["enabled"]:
        return "Produk ini lagi gak tersedia."
    return None


async def build_lines(
    db: Database, raw_lines, *, drop_missing_for: int | None = None
) -> list[CartLine]:
    """Ubah daftar (product_id, quantity) jadi CartLine. Line yang gak bisa
    dibeli tetep dikembaliin dengan `.problem` keisi (biar UI bisa nunjukin
    kenapa), kecuali produknya udah kehapus total -- itu dibuang (dan kalau
    `drop_missing_for` = user_id, barisnya ikut dibersihin dari keranjang).

    Stok dicek PER POOL secara kumulatif: dua line yang narik dari pool
    sama saling ngurangin sisa, jadi total permintaan gak bisa lewat dari
    isi pool walau tiap line sendirian kelihatan cukup."""
    lines: list[CartLine] = []
    for product_id, quantity in raw_lines:
        product = await products_q.get_product(db, int(product_id))
        if product is None:
            if drop_missing_for is not None:
                await cart_q.remove(db, drop_missing_for, int(product_id))
            continue
        group = (
            await product_groups_q.get_product_group(db, product["product_group_id"])
            if product["product_group_id"] else None
        )
        quantity = max(1, int(quantity))
        unit_price = calculate_final_price(
            product["base_price"], product["discount_type"], product["discount_value"]
        )
        line = CartLine(
            product=product, group=group, quantity=quantity, unit_price=unit_price,
            line_total=round(unit_price * quantity, 2), source=resolve_source(product, group),
        )
        line.problem = await _product_unavailable_reason(db, product, group)
        lines.append(line)

    remaining: dict[tuple[str, int], int] = {}
    for line in lines:
        key = line.source.pool_key
        if key is None or line.problem:
            continue
        left = remaining.setdefault(key, int(line.source.available))
        need = line.stock_needed
        if need > left:
            line.problem = (
                f"Stok gak cukup buat {line.quantity}x ({format_stock(line.source)})."
                if left == line.source.available
                else "Stok gabungan produk di keranjang ini gak cukup."
            )
        else:
            remaining[key] = left - need
    return lines


def first_problem(lines: list[CartLine]) -> str | None:
    """Masalah pertama yang ngeblok checkout (None = aman). Termasuk
    campuran mata uang."""
    for line in lines:
        if line.problem:
            return f"**{line.name}**: {line.problem}"
    return currency_problem(lines)


def currency_problem(lines: list[CartLine]) -> str | None:
    currencies = {line.currency for line in lines}
    if len(currencies) > 1:
        return (
            "Keranjang cuma bisa berisi produk dengan mata uang yang sama "
            f"(sekarang campur: {', '.join(sorted(currencies))}). Checkout produknya terpisah ya."
        )
    return None


def subtotal(lines: list[CartLine]) -> float:
    return round(sum(line.line_total for line in lines), 2)


async def load_cart(db: Database, user_id: int) -> list[CartLine]:
    rows = await cart_q.list_cart(db, user_id)
    return await build_lines(
        db, [(r["product_id"], r["quantity"]) for r in rows], drop_missing_for=user_id
    )


# ============================================================================
# UBAH KERANJANG
# ============================================================================

async def try_set_quantity(
    db: Database, user_id: int, product_id: int, quantity: int
) -> tuple[bool, str | None]:
    """Set qty baris keranjang dengan validasi stok. quantity <= 0 = hapus.
    Kalau kenaikan bikin stok (termasuk pool bersama) gak cukup, qty
    dibalikin ke nilai semula dan (False, alasan) dikembaliin. Penurunan
    selalu lolos."""
    quantity = min(int(quantity), MAX_LINE_QTY)
    existing = await cart_q.get_line(db, user_id, product_id)
    old = int(existing["quantity"]) if existing else 0
    if quantity <= 0:
        await cart_q.remove(db, user_id, product_id)
        return True, None

    await cart_q.set_quantity(db, user_id, product_id, quantity)
    if quantity <= old:
        return True, None

    rows = await cart_q.list_cart(db, user_id)
    lines = await build_lines(db, [(r["product_id"], r["quantity"]) for r in rows])
    bad = next((l for l in lines if l.product_id == product_id and l.problem), None)
    if bad is not None:
        await cart_q.set_quantity(db, user_id, product_id, old)  # old = 0 -> terhapus
        return False, bad.problem
    return True, None


async def add_to_cart(
    db: Database, user_id: int, product_id: int, quantity: int = 1
) -> tuple[bool, str]:
    """Tambah `quantity` produk ke keranjang. Return (ok, pesan buat user)."""
    product = await products_q.get_product(db, product_id)
    if product is None:
        return False, "Produk gak ketemu."
    group = (
        await product_groups_q.get_product_group(db, product["product_group_id"])
        if product["product_group_id"] else None
    )
    reason = await _product_unavailable_reason(db, product, group)
    if reason:
        return False, reason

    rows = await cart_q.list_cart(db, user_id)
    existing = next((r for r in rows if r["product_id"] == product_id), None)
    if existing is None and len(rows) >= MAX_CART_LINES:
        return False, f"Keranjang penuh (maksimal {MAX_CART_LINES} produk berbeda). Checkout atau hapus dulu salah satunya."

    current_lines = await build_lines(db, [(r["product_id"], r["quantity"]) for r in rows])
    other_currencies = {l.currency for l in current_lines if l.product_id != product_id}
    if other_currencies and product["currency_label"] not in other_currencies:
        return False, (
            f"Keranjang kamu isinya produk bermata uang **{', '.join(sorted(other_currencies))}**, "
            f"sedangkan produk ini **{product['currency_label']}**. Satu keranjang cuma bisa satu mata uang -- "
            "checkout atau kosongin keranjang dulu."
        )

    old = int(existing["quantity"]) if existing else 0
    new_qty = old + max(1, int(quantity))
    if new_qty > MAX_LINE_QTY:
        return False, f"Maksimal {MAX_LINE_QTY} per produk (di keranjang kamu sudah {old})."

    ok, problem = await try_set_quantity(db, user_id, product_id, new_qty)
    if not ok:
        return False, problem or "Stok gak cukup."
    return True, f"**{quantity}x {product['name']}** masuk keranjang (total di keranjang: {new_qty})."


# ============================================================================
# DYNAMIC CHECKOUT FIELD (gabungan semua Category Type di keranjang)
# ============================================================================

def _with_suffix(label: str, name: str) -> str:
    """label + " (name)" dipotong biar muat di batas label TextInput Discord."""
    suffix = f" ({name})"
    if len(label) + len(suffix) > MODAL_LABEL_MAX:
        room = max(1, MODAL_LABEL_MAX - len(label) - 3)
        suffix = f" ({name[:room]}\u2026)" if room > 1 else ""
    return (label + suffix)[:MODAL_LABEL_MAX]


async def collect_fields(db: Database, lines: list[CartLine]) -> list[dict]:
    """Field checkout buat semua produk di `lines`, dalam urutan: field
    Category Type, lalu field TAMBAHAN Product Type-nya (kalau ada).

    Tiap Category Type dan tiap Product Type ditanyain SEKALI aja walau ada
    beberapa produk darinya. Field Product Type sifatnya tambahan -- gak
    pernah ngegantiin field Category Type.

    Label dikasih akhiran "(Nama)" HANYA kalau jawabannya bisa ketuker:
      * keranjang nyampur lebih dari satu Category Type yang punya field
        -> field tiap tipe dikasih akhiran nama tipenya;
      * ada lebih dari satu Product Type yang punya field, ATAU label field
        Product Type sama dengan label field lain
        -> field Product Type dikasih akhiran nama Product Type-nya.
    Return list dict (siap dipake DynamicFieldsModal)."""
    type_ids: list[int] = []
    for line in lines:
        ct_id = int(line.product["category_type_id"])
        if ct_id not in type_ids:
            type_ids.append(ct_id)

    # (kind, nama_sumber, rows) urut: tipe, lalu Product Type di bawah tipe itu
    sources: list[tuple[str, str, list]] = []
    for ct_id in type_ids:
        type_fields = await fields_q.list_fields(db, ct_id)
        if type_fields:
            category_type = await category_types_q.get_category_type(db, ct_id)
            sources.append(("type", category_type["name"], type_fields))

        seen_groups: list[int] = []
        for line in lines:
            if int(line.product["category_type_id"]) != ct_id or line.group is None:
                continue
            gid = int(line.group["id"])
            if gid in seen_groups:
                continue
            seen_groups.append(gid)
            group_fields = await fields_q.list_group_fields(db, gid)
            if group_fields:
                sources.append(("group", line.group["name"], group_fields))

    prefix_types = sum(1 for kind, _, _ in sources if kind == "type") > 1
    prefix_groups = sum(1 for kind, _, _ in sources if kind == "group") > 1
    label_counts: dict[str, int] = {}
    for _, _, rows in sources:
        for f in rows:
            key = f["label"].strip().lower()
            label_counts[key] = label_counts.get(key, 0) + 1

    result: list[dict] = []
    for kind, name, rows in sources:
        for f in rows:
            row = dict(f)
            if kind == "type" and prefix_types:
                row["label"] = _with_suffix(row["label"], name)
            elif kind == "group" and (prefix_groups or label_counts[f["label"].strip().lower()] > 1):
                row["label"] = _with_suffix(row["label"], name)
            else:
                row["label"] = row["label"][:MODAL_LABEL_MAX]
            result.append(row)
    return result


# ============================================================================
# STOK -- potong & balikin
# ============================================================================

async def _decrement(db: Database, kind: str, source_id: int, amount: int) -> bool:
    if kind == SOURCE_GROUP:
        return await product_groups_q.try_decrement_stock(db, source_id, amount)
    if kind == SOURCE_PRODUCT:
        return await products_q.try_decrement_stock(db, source_id, amount)
    return True


async def _increment(db: Database, kind: str, source_id: int, amount: int) -> None:
    if kind == SOURCE_GROUP:
        await product_groups_q.increment_stock(db, source_id, amount)
    elif kind == SOURCE_PRODUCT:
        await products_q.increment_stock(db, source_id, amount)


async def reserve_lines(
    db: Database, lines: list[CartLine]
) -> tuple[bool, str | None, list[dict]]:
    """Potong stok buat SEMUA line, atau gak sama sekali. Tiap potongan
    atomik; kalau satu line gagal, yang udah kepotong dibalikin. Return
    (ok, pesan_error, item_dicts) -- item_dicts siap dikasih ke
    orders_q.create_order_with_items (udah termasuk catatan stok yang
    kepotong)."""
    done: list[dict] = []
    for line in lines:
        src = line.source
        used = line.stock_needed
        if src.unlimited or used <= 0:
            done.append(_item_dict(line, SOURCE_NONE, None, 0))
            continue
        if not await _decrement(db, src.kind, src.source_id, used):
            await release_reserved(db, done)
            return False, f"Yah, stok **{line.name}** baru aja berubah dan gak cukup lagi. Coba cek ulang.", []
        done.append(_item_dict(line, src.kind, src.source_id, used))
    return True, None, done


def _item_dict(line: CartLine, kind: str, source_id: int | None, used: int) -> dict:
    return {
        "product_id": line.product_id,
        "product_name": line.name,
        "quantity": line.quantity,
        "unit_price": line.unit_price,
        "line_total": line.line_total,
        "stock_source": kind,
        "stock_source_id": source_id,
        "stock_used": used,
    }


async def release_reserved(db: Database, item_dicts: list[dict]) -> None:
    """Balikin stok dari hasil reserve_lines() -- dipake kalau order-nya
    gagal dibuat setelah stok kadung kepotong."""
    for it in item_dicts:
        if it["stock_used"] > 0 and it["stock_source"] != SOURCE_NONE:
            await _increment(db, it["stock_source"], it["stock_source_id"], it["stock_used"])


async def release_order_stock(db: Database, order_id: int) -> bool:
    """Balikin stok order ke pool aslinya (cancel / refund / expired).
    Aman dipanggil berkali-kali: cuma panggilan PERTAMA yang beneran
    ngembaliin (lewat orders.stock_reserved sebagai kunci), sisanya no-op.
    Jumlah yang dibalikin = yang TERCATAT kepotong di order_items.stock_used,
    bukan dihitung ulang dari harga/stock_cost sekarang."""
    if not await orders_q.claim_stock_release(db, order_id):
        return False
    for item in await orders_q.list_order_items(db, order_id):
        if item["stock_used"] > 0 and item["stock_source"] != SOURCE_NONE and item["stock_source_id"]:
            try:
                await _increment(db, item["stock_source"], item["stock_source_id"], item["stock_used"])
            except Exception:  # noqa: BLE001
                logger.exception("Gagal balikin stok item #%s order #%s.", item["id"], order_id)
    return True


# ============================================================================
# TEKS
# ============================================================================

def describe_lines(lines, *, currency: str | None = None, limit: int = 15) -> str:
    """Daftar item satu baris per produk, buat embed. `lines` boleh
    CartLine ATAU row order_items (sama-sama punya nama/qty/total)."""
    out = []
    for line in list(lines)[:limit]:
        if isinstance(line, CartLine):
            name, qty, total, cur = line.name, line.quantity, line.line_total, line.currency
        else:
            name, qty, total, cur = line["product_name"], line["quantity"], line["line_total"], currency or ""
        out.append(f"- {qty}x **{name}** -- {total:,.2f} {cur}".rstrip())
    if len(lines) > limit:
        out.append(f"-# ...dan {len(lines) - limit} lainnya")
    return "\n".join(out)
