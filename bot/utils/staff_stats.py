"""Gabungin statistik per-staff dari semua sumber (event order, respon
ticket, claim, bukti pengiriman) -- dipake bot.cogs.staff_stats. Dipisah dari
cog biar bisa dites tanpa Discord.

Waktu proses & waktu respon pake MEDIAN (bukan rata-rata): satu order yang
nyangkut seharian gak boleh ngerusak angka staff yang biasanya cepet."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from statistics import median

from bot.database.queries import staff_metrics as metrics_q
from bot.utils.store_status import WIB

PERIODS = {
    "today": "Hari ini",
    "7d": "7 hari",
    "30d": "30 hari",
    "all": "Semua waktu",
}


def period_since(period: str, now: datetime | None = None) -> str | None:
    """Batas awal periode sebagai teks UTC format SQLite ('YYYY-MM-DD HH:MM:SS'),
    None = semua waktu. 'Hari ini' = sejak 00:00 WIB (bukan UTC)."""
    now = now or datetime.now(timezone.utc)
    if period == "today":
        start = now.astimezone(WIB).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "7d":
        start = now - timedelta(days=7)
    elif period == "30d":
        start = now - timedelta(days=30)
    else:
        return None
    return start.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _blank() -> dict:
    return {
        "completed": 0, "paid": 0, "cancelled": 0, "refunded": 0, "shipments": 0, "claimed": 0,
        "responses": 0, "median_handle": None, "median_response": None,
    }


async def gather(db, since: str | None) -> dict[int, dict]:
    """{staff_id: statistik}. Cuma staff yang punya aktivitas di periode itu."""
    stats: dict[int, dict] = {}

    def entry(staff_id: int) -> dict:
        return stats.setdefault(int(staff_id), _blank())

    for row in await metrics_q.order_event_counts(db, since):
        key = {"completed": "completed", "paid": "paid", "cancelled": "cancelled", "refunded": "refunded"}.get(row["kind"])
        if key:
            entry(row["actor_id"])[key] += int(row["n"])

    handling: dict[int, list[float]] = {}
    for row in await metrics_q.handling_seconds(db, since):
        if row["secs"] is not None and row["secs"] >= 0:
            handling.setdefault(int(row["actor_id"]), []).append(float(row["secs"]))
    for staff_id, values in handling.items():
        entry(staff_id)["median_handle"] = median(values)

    responses: dict[int, list[float]] = {}
    for row in await metrics_q.response_seconds(db, since):
        if row["secs"] is not None and row["secs"] >= 0:
            responses.setdefault(int(row["staff_id"]), []).append(float(row["secs"]))
    for staff_id, values in responses.items():
        item = entry(staff_id)
        item["responses"] = len(values)
        item["median_response"] = median(values)

    for row in await metrics_q.shipment_counts(db, since):
        entry(row["staff_user_id"])["shipments"] += int(row["n"])
    for row in await metrics_q.claim_counts(db, since):
        entry(row["claimed_by"])["claimed"] += int(row["n"])
    return stats


def ranked_rows(stats: dict[int, dict], limit: int = 10) -> list[dict]:
    rows = [{"staff_id": staff_id, **values} for staff_id, values in stats.items()]
    rows.sort(key=lambda r: (r["completed"], r["paid"], r["shipments"], r["claimed"]), reverse=True)
    return rows[:limit]
