"""Query helpers buat `panel_reply_buttons` -- isi tombol Reply yang
ditambahin lewat /panel atau /announcement."""

from __future__ import annotations

from bot.database.core import Database


async def create_reply_button(db: Database, label: str, reply_text: str) -> int:
    return await db.execute(
        "INSERT INTO panel_reply_buttons (label, reply_text) VALUES (?, ?)", (label, reply_text)
    )


async def get_reply_button(db: Database, button_id: int):
    return await db.fetchone("SELECT * FROM panel_reply_buttons WHERE id = ?", (button_id,))
