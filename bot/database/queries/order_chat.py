"""Queries buat state panel chat per-order -- SATU pesan yang di-edit
terus di channel order-log, bukan spam pesan baru tiap balesan. Lihat
bot.utils.order_chat buat pemakaiannya."""

from __future__ import annotations

from bot.database.core import Database


async def get_panel(db: Database, order_id: int):
    return await db.fetchone("SELECT * FROM order_chat_panels WHERE order_id = ?", (order_id,))


async def save_panel(
    db: Database, order_id: int, channel_id: int, message_id: int,
    transcript: str, latest_image_url: str | None,
) -> None:
    await db.execute(
        """
        INSERT INTO order_chat_panels (order_id, channel_id, message_id, transcript, latest_image_url, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(order_id) DO UPDATE SET
            channel_id = excluded.channel_id,
            message_id = excluded.message_id,
            transcript = excluded.transcript,
            latest_image_url = excluded.latest_image_url,
            updated_at = datetime('now')
        """,
        (order_id, channel_id, message_id, transcript, latest_image_url),
    )
