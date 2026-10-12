"""
Logika SLA staff: kapan sebuah order/ticket dianggap "kelamaan nunggu"
dan perlu diingatkan. Tiga jenis:

  proof   bukti bayar udah masuk tapi belum diverifikasi staff
  order   order udah lunas tapi belum selesai
  ticket  ticket masih open, belum dibalas & belum di-claim staff

PENTING -- jam hitungnya CUMA JAM TOKO BUKA (jam operasional /storestatus).
Order yang masuk pas toko tutup gak dianggap telat dan staff gak diteror
tengah malam; hitungannya mulai jalan lagi begitu toko buka. Selama toko
TUTUP atau status khusus "Perbaikan Sistem" aktif, gak ada pengingat sama
sekali. Order/ticket yang udah lebih dari 24 jam diabaikan (kemungkinan
terbengkalai -- bukan urusan pengingat, tapi urusan bersih-bersih manual),
dan tiap item cuma diingetin maksimal beberapa kali biar gak spam.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from bot.database.queries import settings as settings_q
from bot.database.queries import staff_metrics as metrics_q
from bot.utils.store_status import WIB, _parse_hhmm

MAX_AGE_HOURS = 24

DEFAULTS = {
    "sla_proof_minutes": 10,     # bukti bayar -> diverifikasi
    "sla_order_minutes": 60,     # lunas -> selesai
    "sla_ticket_minutes": 10,    # ticket dibuka -> dibalas staff
    "sla_repeat_minutes": 30,    # jeda antar pengingat buat item yang sama
    "sla_max_reminders": 3,      # maksimal pengingat per item
    "pay_reminder_minutes": 10,  # ingetin pembeli X menit sebelum batas bayar (0 = mati)
}


@dataclass(frozen=True, slots=True)
class SlaConfig:
    enabled: bool
    proof_minutes: int
    order_minutes: int
    ticket_minutes: int
    repeat_minutes: int
    max_reminders: int
    pay_reminder_minutes: int
    channel_id: int | None


async def _int_setting(db, key: str) -> int:
    value = await settings_q.get_setting(db, key)
    try:
        return max(int(value), 0) if value not in (None, "") else DEFAULTS[key]
    except ValueError:
        return DEFAULTS[key]


async def load_config(db) -> SlaConfig:
    enabled = (await settings_q.get_setting(db, "sla_enabled")) != "0"  # default NYALA
    channel = await settings_q.get_setting(db, "sla_channel_id")
    return SlaConfig(
        enabled=enabled,
        proof_minutes=await _int_setting(db, "sla_proof_minutes"),
        order_minutes=await _int_setting(db, "sla_order_minutes"),
        ticket_minutes=await _int_setting(db, "sla_ticket_minutes"),
        repeat_minutes=max(await _int_setting(db, "sla_repeat_minutes"), 1),
        max_reminders=max(await _int_setting(db, "sla_max_reminders"), 1),
        pay_reminder_minutes=await _int_setting(db, "pay_reminder_minutes"),
        channel_id=int(channel) if channel and str(channel).isdigit() else None,
    )


# ============================================================================
# Waktu
# ============================================================================

def parse_sqlite_utc(value: str | None) -> datetime | None:
    """'YYYY-MM-DD HH:MM:SS' (UTC, format SQLite datetime('now')) atau ISO 8601
    -> datetime sadar-zona. None kalau gak kebaca."""
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def open_seconds_between(start: datetime, end: datetime, open_time: str, close_time: str) -> float:
    """Berapa detik dari [start, end] yang jatuh di JAM TOKO BUKA (WIB).
    Nanganin jam yang ngelewatin tengah malam (misal 07:30-01:20) dan toko
    24 jam (buka == tutup)."""
    if end <= start:
        return 0.0
    open_t, close_t = _parse_hhmm(open_time), _parse_hhmm(close_time)
    if open_t == close_t:
        return (end - start).total_seconds()
    start_w, end_w = start.astimezone(WIB), end.astimezone(WIB)
    total = 0.0
    day = (start_w - timedelta(days=1)).date()
    last = (end_w + timedelta(days=1)).date()
    while day <= last:
        window_start = datetime.combine(day, open_t, tzinfo=WIB)
        window_end = datetime.combine(day, close_t, tzinfo=WIB)
        if close_t <= open_t:
            window_end += timedelta(days=1)  # jendela buka muter lewat tengah malam
        low, high = max(window_start, start_w), min(window_end, end_w)
        if high > low:
            total += (high - low).total_seconds()
        day += timedelta(days=1)
    return total


def format_duration(seconds: float) -> str:
    # Bulatin ke detik terdekat dulu: selisih julianday() SQLite punya error
    # desimal, jadi "tepat 1 menit" bisa kebaca 59.9999 detik.
    minutes = max(int(round(seconds) // 60), 0)
    if minutes < 1:
        return "kurang dari 1 menit"
    hours, mins = divmod(minutes, 60)
    if hours and mins:
        return f"{hours} jam {mins} menit"
    if hours:
        return f"{hours} jam"
    return f"{mins} menit"


# ============================================================================
# Kumpulin yang kelamaan
# ============================================================================

@dataclass(slots=True)
class Alert:
    kind: str           # proof | order | ticket
    ref_id: int         # order id / ticket id
    user_id: int
    waited: float       # detik (jam toko buka)
    reminders_sent: int
    channel_id: int | None = None   # buat ticket
    amount: float | None = None
    currency: str | None = None


async def collect_alerts(
    db, cfg: SlaConfig, open_time: str, close_time: str, now: datetime | None = None
) -> list[Alert]:
    now = now or datetime.now(timezone.utc)
    sources = (
        ("proof", await metrics_q.list_proofs_waiting_verification(db), cfg.proof_minutes),
        ("order", await metrics_q.list_paid_not_completed(db), cfg.order_minutes),
        ("ticket", await metrics_q.list_tickets_without_response(db), cfg.ticket_minutes),
    )
    alerts: list[Alert] = []
    for kind, rows, threshold_minutes in sources:
        if threshold_minutes <= 0:
            continue  # 0 = jenis ini dimatiin
        for row in rows:
            since = parse_sqlite_utc(row["since"])
            if since is None or now - since > timedelta(hours=MAX_AGE_HOURS):
                continue
            waited = open_seconds_between(since, now, open_time, close_time)
            if waited < threshold_minutes * 60:
                continue
            state = await metrics_q.get_sla_state(db, kind, row["id"])
            sent = int(state["count"]) if state else 0
            if sent >= cfg.max_reminders:
                continue
            if state:
                last = parse_sqlite_utc(state["last_sent_at"])
                if last and now - last < timedelta(minutes=cfg.repeat_minutes):
                    continue
            keys = row.keys()
            alerts.append(Alert(
                kind=kind, ref_id=int(row["id"]), user_id=int(row["user_id"]), waited=waited, reminders_sent=sent,
                channel_id=int(row["channel_id"]) if "channel_id" in keys else None,
                amount=float(row["total_price"]) if "total_price" in keys else None,
                currency=row["currency_label"] if "currency_label" in keys else None,
            ))
    return alerts
