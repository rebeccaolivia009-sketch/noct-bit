"""
Command staff: /invite -- invite tracker buat event berhadiah "siapa
invite paling banyak".

Cara kerja deteksi "siapa invite siapa" (Discord gak ngasih info ini
langsung): bot nyimpen snapshot `uses` count SEMUA invite tiap guild pas
startup (_cache_guild_invites), terus tiap ada member baru gabung
(on_member_join), snapshot lama dibandingin sama guild.invites() yang
baru buat nemuin invite mana yang `uses`-nya nambah 1 -- itu invite yang
kepake. Pemilik invite-nya diambil dari invite_links DULUAN (kalau
invite-nya digenerate BOT lewat tombol "Generate Link Server" -- WAJIB
dicek duluan, soalnya invite yang dibikin bot bakal invite.inviter =
akun BOT itu sendiri, bukan user yang klik tombol), fallback ke
invite.inviter kalau invite-nya dibikin manual staff lewat UI Discord
biasa.

Anti-curang "full tracking" (sesuai keputusan): pas member yang
ke-attribute ke invite keluar lagi (on_member_remove), row-nya di
invite_members ditandain non-aktif -- otomatis ngurangin hitungan top
inviter tanpa perlu job terjadwal. Leaderboard di-refresh REAL-TIME abis
tiap join/leave (sesuai keputusan juga), plus tetep ada
/invite refresh buat trigger manual.

Bot BUTUH permission "Manage Server" di server itu buat bisa liat
invite.uses/.inviter lewat guild.invites() -- tanpa itu deteksi bakal
selalu gagal (di-log warning sekali per guild di _cache_guild_invites,
bukan tiap ada yang join, biar log gak kebanjiran).
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.core.logger import logger
from bot.database.queries import invites as invites_q
from bot.database.queries import settings as settings_q
from bot.ui import embeds
from bot.ui.views import InvitePanelView
from bot.utils.helpers import RuntimeSettings, guild_scoped_key
from bot.utils.invite_tracker import refresh_invite_leaderboard
from bot.utils.permissions import staff_only


def _parse_custom_emoji(value: str | None) -> discord.PartialEmoji | None:
    """Validasi emoji buat tombol panel -- terima emoji custom SERVER MANA
    PUN (format <:nama:id> / <a:nama:id>) ATAU emoji unicode biasa. Return
    None kalau kosong (caller pake default bawaan). Raise ValueError kalau
    formatnya gak kebaca sama sekali. Sama persis kayak helper di
    bot.cogs.badge -- didupliceate kecil di sini biar dua cog gak
    saling-import cuma buat satu fungsi kecil ini."""
    if not value or not value.strip():
        return None
    value = value.strip()
    try:
        return discord.PartialEmoji.from_str(value)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(
            f"Format emoji `{value}` gak kebaca. Pake emoji unicode biasa, atau emoji custom "
            "server (ketik `\\:namaemoji:` di chat dulu buat dapet kode aslinya, terus tempel di sini)."
        ) from exc


class InviteTrackerCog(commands.Cog):
    """Deteksi siapa invite siapa + leaderboard real-time + panel event."""

    invite_group = app_commands.Group(
        name="invite", description="Atur invite tracker & panel event berhadiah.", guild_only=True
    )
    settings_group = app_commands.Group(
        name="settings", description="Atur channel notif, leaderboard, dan aturan main.", parent=invite_group
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # {guild_id: {invite_code: uses}} -- snapshot SEMENTARA doang,
        # gak disimpen ke DB (lihat docstring bot.utils.invite_tracker).
        # Di-refresh penuh tiap ada create/delete invite atau abis dipake
        # diff pas member join.
        self._cache: dict[int, dict[str, int]] = {}
        self._warned_no_permission: set[int] = set()

    async def cog_load(self) -> None:
        # Nunggu bot beneran ready (guild cache siap) sebelum nyoba fetch
        # invite tiap guild -- kalau langsung jalan pas cog_load, guild
        # cache mungkin masih kosong.
        self.bot.loop.create_task(self._cache_all_guilds_when_ready())

    async def _cache_all_guilds_when_ready(self) -> None:
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            await self._cache_guild_invites(guild)
        logger.info("Invite tracker: cache invite awal beres buat %d guild.", len(self.bot.guilds))

    async def _cache_guild_invites(self, guild: discord.Guild) -> dict[str, int]:
        try:
            current = await guild.invites()
        except discord.Forbidden:
            if guild.id not in self._warned_no_permission:
                logger.warning(
                    "Invite tracker: bot gak punya permission 'Manage Server' di guild %s (%s) -- "
                    "deteksi invite gak bakal jalan sampe permission-nya dikasih.",
                    guild.name, guild.id,
                )
                self._warned_no_permission.add(guild.id)
            self._cache[guild.id] = {}
            return self._cache[guild.id]
        except discord.HTTPException:
            logger.exception("Invite tracker: gagal fetch invite guild %s.", guild.id)
            return self._cache.get(guild.id, {})

        snapshot = {inv.code: inv.uses or 0 for inv in current}
        self._cache[guild.id] = snapshot
        return snapshot

    # -- Listener: jaga cache tetep akurat begitu ada CRUD invite -----------

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite) -> None:
        self._cache.setdefault(invite.guild.id, {})[invite.code] = invite.uses or 0

    @commands.Cog.listener()
    async def on_invite_delete(self, invite: discord.Invite) -> None:
        self._cache.get(invite.guild.id, {}).pop(invite.code, None)

    # -- Listener: deteksi join & leave ---------------------------------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return  # bot lain yang ditambahin ke server gak dihitung invite

        guild = member.guild
        before = self._cache.get(guild.id, {})
        after = await self._cache_guild_invites(guild)

        used_code = next((code for code, uses in after.items() if uses > before.get(code, 0)), None)
        if used_code is None:
            logger.info(
                "Invite tracker: gak nemu invite yang kepake buat %s join di guild %s "
                "(mungkin lewat vanity URL, atau bot kehilangan permission Manage Server).",
                member, guild.id,
            )
            return

        db = self.bot.db
        inviter_id = await invites_q.get_link_owner(db, used_code)
        if inviter_id is None:
            match = next((inv for inv in await guild.invites() if inv.code == used_code), None)
            if match and match.inviter:
                inviter_id = match.inviter.id

        if inviter_id is None or inviter_id == member.id:
            return  # gak ketauan siapa pemiliknya, atau invite ke diri sendiri

        await invites_q.upsert_member(db, guild.id, member.id, inviter_id, used_code)
        total = await invites_q.get_active_count(db, guild.id, inviter_id)

        await self._send_notif(guild, inviter_id, member, total, joined=True)
        await refresh_invite_leaderboard(self.bot)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        db = self.bot.db
        guild = member.guild

        inviter_id = await invites_q.get_inviter_for_member(db, guild.id, member.id)
        changed = await invites_q.mark_member_left(db, guild.id, member.id)
        if not changed:
            return

        total = await invites_q.get_active_count(db, guild.id, inviter_id)
        await self._send_notif(guild, inviter_id, member, total, joined=False)
        await refresh_invite_leaderboard(self.bot)

    async def _send_notif(
        self, guild: discord.Guild, inviter_id: int, member: discord.Member | discord.User,
        total: int, *, joined: bool,
    ) -> None:
        runtime = RuntimeSettings(self.bot.db)
        channel_id = await runtime.invite_notif_channel_id()
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return

        inviter = self.bot.get_user(inviter_id)
        inviter_display = inviter.mention if inviter else f"<@{inviter_id}>"

        if joined:
            title = "Invite Baru Masuk!"
            description = (
                f"{inviter_display} ngundang {member.mention} masuk server.\n\n"
                f"Total invite {inviter_display} sekarang: **{total} orang** (yang masih di server)."
            )
        else:
            title = "Undangan Keluar Server"
            description = (
                f"**{member}** (diundang {inviter_display}) keluar dari server.\n\n"
                f"Total invite {inviter_display} sekarang: **{total} orang** (yang masih di server)."
            )

        try:
            await channel.send(embed=embeds.info_embed(title, description))
        except discord.HTTPException:
            logger.exception("Gagal kirim notif invite tracker di channel %s.", channel_id)

    # -- /invite panel ----------------------------------------------------------

    @invite_group.command(
        name="panel", description="Posting panel invite tracker (generate link + aturan main) di channel ini."
    )
    @app_commands.describe(
        title="Judul panel",
        description="Isi teks panel",
        banner="Gambar banner full-width paling atas (opsional)",
        footer_text="Teks kecil credit di paling bawah panel",
        emoji_generate="Emoji tombol Generate Link -- boleh emoji custom server (opsional)",
        emoji_rules="Emoji tombol Aturan Main -- boleh emoji custom server (opsional)",
    )
    @staff_only()
    async def panel(
        self,
        interaction: discord.Interaction,
        title: str = "\U0001F3AF INVITE EVENT -- NOCTRA STORE",
        description: str = (
            "Ajakin temen kamu gabung ke sini -- tiap orang yang join lewat link kamu bakal kehitung "
            "otomatis. Makin banyak yang kamu invite, makin gede peluang kamu menang hadiahnya!"
        ),
        banner: discord.Attachment | None = None,
        footer_text: str = "-# NOCTRA STORE  \u2022  Invite Event",
        emoji_generate: str | None = None,
        emoji_rules: str | None = None,
    ) -> None:
        if banner is not None and (not banner.content_type or not banner.content_type.startswith("image/")):
            await interaction.response.send_message(
                embed=embeds.error_embed("Banner harus berupa gambar."), ephemeral=True
            )
            return

        try:
            parsed_generate = _parse_custom_emoji(emoji_generate)
            parsed_rules = _parse_custom_emoji(emoji_rules)
        except ValueError as exc:
            await interaction.response.send_message(embed=embeds.error_embed(str(exc)), ephemeral=True)
            return

        view_kwargs: dict = {"title": title, "description": description, "footer_text": footer_text}
        if banner is not None:
            view_kwargs["banner_url"] = banner.url
        if parsed_generate is not None:
            view_kwargs["emoji_generate"] = parsed_generate
        if parsed_rules is not None:
            view_kwargs["emoji_rules"] = parsed_rules

        await interaction.channel.send(view=InvitePanelView(**view_kwargs))
        await interaction.response.send_message(
            embed=embeds.success_embed("Panel invite tracker udah diposting."), ephemeral=True
        )

    # -- /invite settings ---------------------------------------------------------

    @settings_group.command(name="notif_channel", description="Atur channel notif join/leave invite tracker.")
    @app_commands.describe(channel="Channel buat notif siapa invite siapa")
    @staff_only()
    async def notif_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await settings_q.set_setting(self.bot.db, "invite_notif_channel_id", str(channel.id))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Channel notif invite tracker diatur ke {channel.mention}."), ephemeral=True
        )

    @settings_group.command(name="leaderboard_channel", description="Atur channel gambar leaderboard invite tracker.")
    @app_commands.describe(channel="Channel buat gambar leaderboard invite")
    @staff_only()
    async def leaderboard_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await settings_q.set_setting(self.bot.db, "invite_leaderboard_channel_id", str(channel.id))
        await settings_q.set_setting(
            self.bot.db, guild_scoped_key("invite_leaderboard_message_id", interaction.guild_id), ""
        )
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Channel leaderboard invite diatur ke {channel.mention}. Bakal keposting otomatis begitu ada "
                "join/leave baru, atau pake `/invite refresh` buat posting sekarang."
            ),
            ephemeral=True,
        )

    @settings_group.command(name="rules", description="Atur teks aturan main yang muncul pas tombol 'Aturan Main' diklik.")
    @app_commands.describe(teks="Isi aturan main event invite")
    @staff_only()
    async def rules(self, interaction: discord.Interaction, teks: str) -> None:
        await settings_q.set_setting(self.bot.db, "invite_rules_text", teks)
        await interaction.response.send_message(
            embed=embeds.success_embed("Aturan main invite event udah diatur."), ephemeral=True
        )

    # -- /invite top-level lainnya ------------------------------------------------

    @invite_group.command(name="refresh", description="Posting/refresh gambar leaderboard invite tracker manual sekarang juga.")
    @staff_only()
    async def refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok = await refresh_invite_leaderboard(self.bot)
        if ok:
            await interaction.followup.send(embed=embeds.success_embed("Leaderboard invite udah di-refresh."), ephemeral=True)
        else:
            await interaction.followup.send(
                embed=embeds.error_embed(
                    "Gak bisa refresh. Pastiin `/invite settings leaderboard_channel` udah diatur dan udah "
                    "ada minimal satu invite yang kehitung."
                ),
                ephemeral=True,
            )

    @invite_group.command(name="stats", description="Liat total invite (yang masih di server) buat satu user.")
    @app_commands.describe(user="Kosongin buat liat punya kamu sendiri")
    @staff_only()
    async def stats(self, interaction: discord.Interaction, user: discord.User | None = None) -> None:
        target = user or interaction.user
        total = await invites_q.get_active_count(self.bot.db, interaction.guild_id, target.id)
        await interaction.response.send_message(
            embed=embeds.info_embed(
                "Invite Tracker", f"{target.mention} udah ngundang **{total} orang** (yang masih di server)."
            ),
            ephemeral=True,
        )

    @invite_group.command(name="reset", description="[HATI-HATI] Reset semua hitungan invite tracker server ini ke nol.")
    @staff_only()
    async def reset(self, interaction: discord.Interaction) -> None:
        await invites_q.reset_guild(self.bot.db, interaction.guild_id)
        await refresh_invite_leaderboard(self.bot)
        await interaction.response.send_message(
            embed=embeds.success_embed(
                "Semua hitungan invite tracker server ini udah direset ke nol. Link yang udah digenerate orang "
                "tetep valid dipake buat event berikutnya."
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(InviteTrackerCog(bot))
