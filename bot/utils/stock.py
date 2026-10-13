"""
Aturan stok -- fungsi murni (gak nyentuh DB) biar gampang dites dan dipake
dari UI maupun logic order.

Dua TIPE stok:
  * unit   -- satuan. Tiap qty yang dibeli ngurangin stok 1. (Perilaku lama.)
  * amount -- jumlah/ribuan, misal stok Robux. Tiap qty ngurangin stok
              sebesar `products.stock_cost`. Contoh: pool 10.000 Robux,
              produk "800 Robux" punya stock_cost = 800, beli 3x ->
              stok berkurang 2.400 (BUKAN 3).

Dua SUMBER stok:
  * product -- stok punya produk itu sendiri (products.stock_quantity).
  * group   -- pool bersama di Product Type (product_groups.stock_quantity,
               shared_stock = 1): semua produk di bawahnya narik dari
               satu pool yang sama.
Kalau produk masuk Product Type yang shared_stock-nya nyala, pool grup
SELALU menang atas stok produknya sendiri.
"""

from __future__ import annotations

from dataclasses import dataclass

UNIT = "unit"
AMOUNT = "amount"

SOURCE_NONE = "none"
SOURCE_PRODUCT = "product"
SOURCE_GROUP = "group"


@dataclass(frozen=True)
class StockSource:
    kind: str                # none | product | group
    source_id: int | None    # id baris products / product_groups yang dipotong
    mode: str                # unit | amount
    available: int | None    # None = unlimited
    label: str | None        # misal "Robux" (cuma buat tampilan)
    cost: int                # stok yang kepake per 1 qty

    @property
    def unlimited(self) -> bool:
        return self.kind == SOURCE_NONE

    @property
    def pool_key(self) -> tuple[str, int] | None:
        """Kunci buat ngelompokin line yang narik dari pool yang sama."""
        return None if self.unlimited else (self.kind, int(self.source_id))

    def max_quantity(self) -> int | None:
        """Qty maksimal yang bisa dibeli dari sisa pool (None = tanpa batas)."""
        if self.available is None:
            return None
        return max(0, self.available // max(1, self.cost))

    def needed(self, quantity: int) -> int:
        return 0 if self.unlimited else quantity * self.cost


def _cost_for(product, mode: str) -> int:
    if mode != AMOUNT:
        return 1
    return max(1, int(product["stock_cost"] or 1))


def resolve_source(product, group=None) -> StockSource:
    """Tentuin stok mana yang dipotong kalau `product` dibeli. `group` =
    row product_groups produk itu (atau None)."""
    if group is not None and group["shared_stock"]:
        mode = group["stock_mode"] or UNIT
        return StockSource(
            SOURCE_GROUP, int(group["id"]), mode, int(group["stock_quantity"]),
            group["stock_label"], _cost_for(product, mode),
        )
    if product["stock_type"] != "manual":
        return StockSource(SOURCE_NONE, None, UNIT, None, None, 1)
    mode = product["stock_mode"] or UNIT
    return StockSource(
        SOURCE_PRODUCT, int(product["id"]), mode, int(product["stock_quantity"]),
        product["stock_label"], _cost_for(product, mode),
    )


def format_stock(source: StockSource, *, with_cost: bool = False) -> str:
    """Teks stok buat customer. with_cost nambahin info "tiap pembelian
    makai X" khusus tipe jumlah."""
    if source.unlimited:
        return "Unlimited"
    unit = f" {source.label}" if source.label else ""
    text = f"Sisa {source.available:,}{unit}"
    if with_cost and source.mode == AMOUNT:
        text += f"  (tiap pembelian makai {source.cost:,}{unit})"
    return text
