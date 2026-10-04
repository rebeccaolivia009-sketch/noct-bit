"""Query helpers buat `panel_reply_buttons` -- isi tombol Reply yang
ditambahin lewat /panel atau /announcement."""

from __future__ import annotations

from bot.database.core import Database


async def create_reply_button(
    db: Database, label: str, reply_text: str,
    image_url: str | None = None, thumbnail_url: str | None = None,
) -> int:
    return await db.execute(
        "INSERT INTO panel_reply_buttons (label, reply_text, image_url, thumbnail_url) VALUES (?, ?, ?, ?)",
        (label, reply_text, image_url, thumbnail_url),
    )


async def update_reply_button(
    db: Database, button_id: int, *,
    label: str, reply_text: str,
    image_url: str | None = None, thumbnail_url: str | None = None,
) -> None:
    """Ubah SEMUA field row yang udah ada -- beda sama create_reply_button
    yang bikin row baru. Dipake buat Edit Reply Button (lihat
    bot.ui.draft_builder_base.ButtonActionView), supaya tombol yang udah
    kepasang di pesan lama (custom_id-nya nunjuk ke button_id yang sama)
    ikut keupdate juga begitu staff ngedit, gak perlu bongkar-pasang
    tombolnya ulang."""
    await db.execute(
        "UPDATE panel_reply_buttons SET label = ?, reply_text = ?, image_url = ?, thumbnail_url = ? WHERE id = ?",
        (label, reply_text, image_url, thumbnail_url, button_id),
    )


async def get_reply_button(db: Database, button_id: int):
    return await db.fetchone("SELECT * FROM panel_reply_buttons WHERE id = ?", (button_id,))
