"""Query helpers for `ticket_types` -- konfigurasi jenis-jenis ticket per
server (Customer Service, Konsultasi, dst), diatur staff lewat /ticket type."""

from __future__ import annotations

from bot.database.core import Database


async def upsert_type(
    db: Database,
    *,
    guild_id: int,
    slug: str,
    label: str,
    description: str | None,
    emoji: str | None,
    category_id: int | None,
    archive_category_id: int | None,
    log_channel_id: int | None,
) -> None:
    """Insert kalau slug ini belum ada di server ini, update kalau udah
    ada -- staff bisa `/ticket type add` ulang pake slug yang sama buat
    ganti label/kategori/dll tanpa perlu command "edit" terpisah. Selalu
    ngaktifin lagi (enabled=1) kalau sebelumnya sempet dinonaktifin."""
    await db.execute(
        """
        INSERT INTO ticket_types
            (guild_id, slug, label, description, emoji, category_id,
             archive_category_id, log_channel_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(guild_id, slug) DO UPDATE SET
            label = excluded.label,
            description = excluded.description,
            emoji = excluded.emoji,
            category_id = excluded.category_id,
            archive_category_id = excluded.archive_category_id,
            log_channel_id = excluded.log_channel_id,
            enabled = 1
        """,
        (guild_id, slug, label, description, emoji, category_id, archive_category_id, log_channel_id),
    )


async def get_by_slug(db: Database, guild_id: int, slug: str):
    return await db.fetchone(
        "SELECT * FROM ticket_types WHERE guild_id = ? AND slug = ?", (guild_id, slug)
    )


async def list_types(db: Database, guild_id: int, *, enabled_only: bool = False):
    if enabled_only:
        return await db.fetchall(
            "SELECT * FROM ticket_types WHERE guild_id = ? AND enabled = 1 ORDER BY position ASC, id ASC",
            (guild_id,),
        )
    return await db.fetchall(
        "SELECT * FROM ticket_types WHERE guild_id = ? ORDER BY position ASC, id ASC", (guild_id,)
    )


async def delete_type(db: Database, guild_id: int, slug: str) -> bool:
    """Return True kalau ada row yang beneran kehapus (dipake caller buat
    ngasih tau staff kalau slug yang diketik salah/gak ketemu)."""
    row = await get_by_slug(db, guild_id, slug)
    if row is None:
        return False
    await db.execute("DELETE FROM ticket_types WHERE guild_id = ? AND slug = ?", (guild_id, slug))
    return True
