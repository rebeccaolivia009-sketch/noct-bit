"""Query helpers for `cards` and `card_requests` -- kartu digital NOCTRA."""

from __future__ import annotations

from bot.database.core import Database


async def get_card_by_user(db: Database, user_id: int):
    return await db.fetchone("SELECT * FROM cards WHERE user_id = ?", (user_id,))


async def card_id_exists(db: Database, card_id: str) -> bool:
    row = await db.fetchone("SELECT 1 FROM cards WHERE card_id = ?", (card_id,))
    return row is not None


async def create_card(db: Database, user_id: int, card_id: str, credit_balance: float) -> int:
    return await db.execute(
        "INSERT INTO cards (user_id, card_id, credit_balance) VALUES (?, ?, ?)",
        (user_id, card_id, credit_balance),
    )


async def add_credit(db: Database, user_id: int, amount: float) -> None:
    await db.execute(
        "UPDATE cards SET credit_balance = credit_balance + ?, updated_at = datetime('now') WHERE user_id = ?",
        (amount, user_id),
    )


async def deduct_credit(db: Database, user_id: int, amount: float) -> None:
    """Dipake belakangan pas Credit kepake buat checkout -- gak ada guard
    saldo cukup/enggak di sini, itu tanggung jawab caller buat ngecek
    get_card_by_user()['credit_balance'] duluan sebelum manggil ini."""
    await db.execute(
        "UPDATE cards SET credit_balance = credit_balance - ?, updated_at = datetime('now') WHERE user_id = ?",
        (amount, user_id),
    )


async def add_rewards(db: Database, user_id: int, noctoins: int, server_points: int) -> None:
    await db.execute(
        """
        UPDATE cards
        SET noctoins = noctoins + ?, server_points = server_points + ?, updated_at = datetime('now')
        WHERE user_id = ?
        """,
        (noctoins, server_points, user_id),
    )


async def add_noctoins(db: Database, user_id: int, amount: int) -> None:
    """Beda dari add_rewards() -- ini dipake buat NGEMBALIIN Noctoins yang
    sebelumnya kepake (order dibatalin/refund abis bayar pake Kartu
    NOCTRA), bukan buat ngasih reward baru, jadi server_points SENGAJA
    gak ikut nambah di sini."""
    await db.execute(
        "UPDATE cards SET noctoins = noctoins + ?, updated_at = datetime('now') WHERE user_id = ?",
        (amount, user_id),
    )


async def deduct_noctoins(db: Database, user_id: int, amount: int) -> None:
    await db.execute(
        "UPDATE cards SET noctoins = noctoins - ?, updated_at = datetime('now') WHERE user_id = ?",
        (amount, user_id),
    )


async def create_request(db: Database, user_id: int, kind: str, amount: float, admin_fee: float) -> int:
    return await db.execute(
        "INSERT INTO card_requests (user_id, kind, amount, admin_fee) VALUES (?, ?, ?, ?)",
        (user_id, kind, amount, admin_fee),
    )


async def get_awaiting_proof_request_for_user(db: Database, user_id: int):
    return await db.fetchone(
        "SELECT * FROM card_requests WHERE user_id = ? AND status = 'awaiting_proof' ORDER BY id DESC LIMIT 1",
        (user_id,),
    )


async def get_open_request_for_user(db: Database, user_id: int):
    """'Open' = masih jalan (belum di-approve/reject) -- dipake buat nolak
    permintaan baru kalau customer masih punya satu yang lagi diproses."""
    return await db.fetchone(
        "SELECT * FROM card_requests WHERE user_id = ? AND status IN ('awaiting_proof', 'pending') "
        "ORDER BY id DESC LIMIT 1",
        (user_id,),
    )


async def get_request(db: Database, request_id: int):
    return await db.fetchone("SELECT * FROM card_requests WHERE id = ?", (request_id,))


async def set_request_proof(db: Database, request_id: int, proof_url: str) -> None:
    await db.execute(
        "UPDATE card_requests SET proof_url = ?, status = 'pending' WHERE id = ?",
        (proof_url, request_id),
    )


async def set_request_status(db: Database, request_id: int, status: str) -> None:
    await db.execute(
        "UPDATE card_requests SET status = ?, resolved_at = datetime('now') WHERE id = ?",
        (status, request_id),
    )
