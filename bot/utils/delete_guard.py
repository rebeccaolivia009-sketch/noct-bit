"""
Penjaga command hapus (/category delete, /category_type delete, /product
delete). Produk yang udah punya riwayat order/review gak bisa dihapus --
riwayat transaksi gak boleh ilang -- jadi daripada nunggu SQLite ngelempar
"FOREIGN KEY constraint failed" (yang di Discord keliatannya cuma "Ada yang
error"), command ngecek duluan dan ngasih alasan + jalan keluarnya.
"""

from __future__ import annotations

import sqlite3

from bot.database.queries import products as products_q

_SHOWN = 8


async def blocked_message(
    db,
    *,
    what: str,
    hide_hint: str,
    product_id: int | None = None,
    category_type_id: int | None = None,
    category_id: int | None = None,
) -> str | None:
    """Return pesan penolakan kalau ada produk dengan riwayat di cakupan
    ini, atau None kalau aman dihapus. `what` = nama yang dihapus (buat
    kalimat), `hide_hint` = cara nyembunyiin sebagai gantinya."""
    rows = await products_q.list_products_with_history(
        db, product_id=product_id, category_type_id=category_type_id, category_id=category_id
    )
    if not rows:
        return None
    names = ", ".join(f"**{r['name']}**" for r in rows[:_SHOWN])
    if len(rows) > _SHOWN:
        names += f", +{len(rows) - _SHOWN} lainnya"
    return (
        f"{what} gak bisa dihapus: {len(rows)} produk di dalamnya udah punya riwayat order/review "
        f"({names}). Riwayat transaksi gak boleh ilang, jadi penghapusannya dibatalin -- "
        f"gak ada yang kehapus sama sekali.\n\n{hide_hint}"
    )


FALLBACK_MESSAGE = (
    "Gak bisa dihapus karena masih dipake data lain (kemungkinan riwayat order/review). "
    "Nonaktifin/sembunyiin aja sebagai gantinya."
)


def is_fk_error(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.IntegrityError) and "FOREIGN KEY" in str(exc).upper()
