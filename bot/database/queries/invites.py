"""Queries buat invite tracker -- kepemilikan link yang digenerate bot
(invite_links) + status member yang ke-track (invite_members). Lihat
komentar di schema.sql buat penjelasan lengkap kenapa dua tabel ini
kepisah dan kenapa invite_members gak ngehapus row pas member keluar."""

from __future__ import annotations

from bot.database.core import Database
from bot.database.queries import settings as settings_q
from bot.utils.helpers import guild_scoped_key


# -- invite_links: kepemilikan invite code yang digenerate BOT ---------------

async def save_invite_link(db: Database, code: str, guild_id: int, owner_id: int) -> None:
    await db.execute(
        "INSERT OR REPLACE INTO invite_links (code, guild_id, owner_id, created_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (code, guild_id, owner_id),
    )


async def get_link_owner(db: Database, code: str) -> int | None:
    row = await db.fetchone("SELECT owner_id FROM invite_links WHERE code = ?", (code,))
    return row["owner_id"] if row else None


async def get_existing_link_for_owner(db: Database, guild_id: int, owner_id: int):
    """Return invite code yang udah pernah digenerate user ini di server
    ini (kalau ada) -- dipake tombol Generate Link biar user gak numpuk
    invite baru tiap klik ulang, tinggal dikasih balik code yang lama."""
    return await db.fetchone(
        "SELECT code FROM invite_links WHERE guild_id = ? AND owner_id = ? ORDER BY created_at DESC LIMIT 1",
        (guild_id, owner_id),
    )


# -- invite_members: status live member yang ke-track ------------------------

async def upsert_member(
    db: Database, guild_id: int, member_id: int, inviter_id: int, invite_code: str | None
) -> None:
    """Dipanggil tiap ada join yang berhasil ke-attribute ke satu invite.
    ON CONFLICT nge-reset baris lama (kalau member ini PERNAH ke-track
    sebelumnya terus keluar-masuk lagi) jadi active lagi dengan inviter
    TERBARU -- rejoin lewat invite (siapapun pemiliknya) dianggep invite
    baru yang sah, bukan curang."""
    await db.execute(
        """
        INSERT INTO invite_members (guild_id, member_id, inviter_id, invite_code, joined_at, left_at, active)
        VALUES (?, ?, ?, ?, datetime('now'), NULL, 1)
        ON CONFLICT(guild_id, member_id) DO UPDATE SET
            inviter_id = excluded.inviter_id,
            invite_code = excluded.invite_code,
            joined_at = excluded.joined_at,
            left_at = NULL,
            active = 1
        """,
        (guild_id, member_id, inviter_id, invite_code),
    )


async def mark_member_left(db: Database, guild_id: int, member_id: int) -> bool:
    """Return True kalau member ini emang lagi ke-track aktif (jadi
    caller tau perlu kirim notif/refresh leaderboard atau enggak) --
    False kalau member ini emang gak pernah ke-attribute ke invite
    manapun (misal join lewat vanity URL, atau join sebelum invite
    tracker ini aktif)."""
    row = await db.fetchone(
        "SELECT active FROM invite_members WHERE guild_id = ? AND member_id = ?",
        (guild_id, member_id),
    )
    if row is None or not row["active"]:
        return False
    await db.execute(
        "UPDATE invite_members SET active = 0, left_at = datetime('now') WHERE guild_id = ? AND member_id = ?",
        (guild_id, member_id),
    )
    return True


async def get_inviter_for_member(db: Database, guild_id: int, member_id: int) -> int | None:
    row = await db.fetchone(
        "SELECT inviter_id FROM invite_members WHERE guild_id = ? AND member_id = ?",
        (guild_id, member_id),
    )
    return row["inviter_id"] if row else None


async def get_active_count(db: Database, guild_id: int, inviter_id: int) -> int:
    row = await db.fetchone(
        "SELECT COUNT(*) AS total FROM invite_members WHERE guild_id = ? AND inviter_id = ? AND active = 1",
        (guild_id, inviter_id),
    )
    return row["total"] if row else 0


async def get_leaderboard(db: Database, guild_id: int, limit: int = 10):
    """Ranking top inviter -- CUMA ngitung member yang masih aktif di
    server (active=1), jadi otomatis 'anti-curang': undangan yang keluar
    lagi gak numpang kehitung."""
    return await db.fetchall(
        """
        SELECT inviter_id, COUNT(*) AS total_invites
        FROM invite_members
        WHERE guild_id = ? AND active = 1
        GROUP BY inviter_id
        ORDER BY total_invites DESC
        LIMIT ?
        """,
        (guild_id, limit),
    )


async def reset_guild(db: Database, guild_id: int) -> None:
    """Bersihin SEMUA histori invite tracker buat satu server -- dipake
    staff lewat /invite reset kalau mau mulai event baru dari nol.
    invite_links (kepemilikan link) SENGAJA gak ikut kehapus -- link yang
    udah digenerate orang tetep valid dipake ulang buat event
    berikutnya, cuma hitungannya yang di-reset."""
    await db.execute("DELETE FROM invite_members WHERE guild_id = ?", (guild_id,))


# -- ID pesan leaderboard invite (per-guild, beda dari Top Spenders yang
# satu-toko-global) -- dipake buat edit-in-place tiap refresh.

async def get_leaderboard_message_id(db: Database, guild_id: int) -> int | None:
    value = await settings_q.get_setting(db, guild_scoped_key("invite_leaderboard_message_id", guild_id))
    return int(value) if value else None


async def set_leaderboard_message_id(db: Database, guild_id: int, message_id: int) -> None:
    await settings_q.set_setting(db, guild_scoped_key("invite_leaderboard_message_id", guild_id), str(message_id))
