"""Query helpers for `panel_drafts` -- draft pesan /panel yang kesimpen
(JSON), biar staff bisa lanjutin edit pesan panel yang udah keposting
kapan aja, gak cuma sekali sesi doang selagi PanelBuilderView masih
nyangkut di memori. Lihat bot.ui.panel_builder buat yang manggil ini."""

from __future__ import annotations

from bot.database.core import Database


async def save_draft(db: Database, message_id: int, channel_id: int, draft_json: str) -> None:
    await db.execute(
        """
        INSERT INTO panel_drafts (message_id, channel_id, draft_json, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(message_id) DO UPDATE SET
            draft_json = excluded.draft_json,
            updated_at = excluded.updated_at
        """,
        (message_id, channel_id, draft_json),
    )


async def get_draft(db: Database, message_id: int):
    return await db.fetchone("SELECT * FROM panel_drafts WHERE message_id = ?", (message_id,))


async def delete_draft(db: Database, message_id: int) -> None:
    await db.execute("DELETE FROM panel_drafts WHERE message_id = ?", (message_id,))
