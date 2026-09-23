"""
Command staff: /welcome dan /joinrole.

/welcome dan /joinrole itu PER-SERVER (guild_scoped_key() di helpers.py) --
tiap server yang bot ini numpang punya pesan sambutan & auto join-role
sendiri-sendiri, gak nyampur kayak fitur toko lainnya (/category, /product,
dst yang masih satu-toko-satu-config global, sengaja gak diubah).

/welcome -- pesan sambutan otomatis pas ada member baru gabung ke server.
Dirender pake Components V2 (bukan embed klasik): thumbnail avatar member
(otomatis, gak perlu diatur), garis Separator di antara tiap bagian
(judul / deskripsi / tanggal gabung / banner / footer), title + deskripsi
+ footer custom (dukung placeholder kayak {mention}/{server}/{membercount}
/{date} dan emoji custom server -- tinggal ketik langsung di field manapun
termasuk footer, gak butuh setup apapun).

Soal {mention}: beda sama embed klasik yang gak pernah ping, di Components
V2 placeholder ini BENERAN ngirim notifikasi ke member yang gabung kalau
dipake di title/description -- nyala/matinya diatur lewat /welcome mention
(lihat _send_welcome, dikontrol pake parameter allowed_mentions).

/joinrole -- auto-assign role pas ada yang gabung, bisa diatur beda buat
member biasa vs bot (misal bot yang ditambahin ke server otomatis dikasih
role "Bots" sementara member manusia dikasih role "Unverified").

Kenapa banner pesan sambutan cuma nerima URL (beda sama /iklan yang bisa
upload attachment): pesan ini diposting OTOMATIS berkali-kali setiap ada
yang gabung, jadi gambarnya harus URL yang beneran persisten
(attachment://... cuma valid buat satu pesan spesifik pas dikirim, gak
bisa dipake ulang buat kiriman di masa depan).
"""

from __future__ import annotations

from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from bot.core.logger import logger
from bot.core.theme import COLOR_ACCENT
from bot.database.queries import settings as settings_q
from bot.ui import components, embeds
from bot.utils.helpers import RuntimeSettings, guild_scoped_key
from bot.utils.permissions import staff_only
from bot.utils.validators import parse_hex_color

DEFAULT_TITLE = "Selamat Datang di {server}! \U0001F44B"
DEFAULT_DESCRIPTION = (
    "Halo {mention}, seneng banget kamu gabung ke **{server}**!\n\n"
    "Kamu member ke-**{membercount}** di sini. Jangan lupa baca rules ya, "
    "dan have fun!"
)
DEFAULT_FOOTER_TEXT = "{server}"

# Placeholder khusus buat modal -- BEDA dari DEFAULT_DESCRIPTION di atas.
# Discord ngebatasin field `placeholder` di TextInput modal maksimal 100
# karakter (beda sama `max_length` value yang boleh sampe 4000), sementara
# DEFAULT_DESCRIPTION sendiri sengaja panjang karena dipake juga sebagai
# fallback isi pesan sambutan beneran (bukan cuma buat placeholder) di
# _build_container_for(). Makanya dipisah biar DEFAULT_DESCRIPTION gak perlu
# dipendekin cuma buat nyesuain limit placeholder.
DESCRIPTION_PLACEHOLDER = "Halo {mention}, seneng banget kamu gabung ke {server}! (dukung {membercount}, {date}, dst.)"

TITLE_MAX_LENGTH = 256
DESCRIPTION_MAX_LENGTH = 4000
FOOTER_MAX_LENGTH = 2048

RoleTarget = Literal["user", "bot"]
ClearTarget = Literal["user", "bot", "all"]

JOIN_ROLE_SETTING_KEYS = {"user": "join_role_user_ids", "bot": "join_role_bot_ids"}


def _render_template(template: str, member: discord.Member) -> str:
    """Ganti placeholder di template jadi data member/server yang beneran.
    Emoji custom server gak butuh apa-apa di sini -- staff tinggal ketik
    langsung kodenya (misal <:sukses:123...>) di title/description/footer,
    Discord yang render otomatis asal bot-nya juga ada di server yang
    sama dengan emoji itu."""
    guild = member.guild
    joined_at = member.joined_at or discord.utils.utcnow()
    joined_ts = int(joined_at.timestamp())
    replacements = {
        "{mention}": member.mention,
        "{user}": str(member),
        "{username}": member.name,
        "{display_name}": member.display_name,
        "{server}": guild.name,
        "{membercount}": f"{guild.member_count:,}" if guild.member_count else "?",
        "{date}": f"<t:{joined_ts}:F>",
    }
    rendered = template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


class WelcomeMessageModal(discord.ui.Modal, title="Atur Pesan Sambutan"):
    """5 field (batas maksimal Modal Discord) buat semua bagian teks pesan
    sambutan yang bisa dikustom. Nilai yang lagi aktif di-prefill biar
    staff gak perlu ngetik ulang dari nol tiap mau ubah dikit."""

    def __init__(self, current: dict[str, str | None], on_submit_callback) -> None:
        super().__init__(timeout=600)
        self._on_submit_callback = on_submit_callback

        self.title_input = discord.ui.TextInput(
            label="Judul",
            style=discord.TextStyle.short,
            required=False,
            max_length=TITLE_MAX_LENGTH,
            placeholder=DEFAULT_TITLE,
            default=current.get("title") or "",
        )
        self.description_input = discord.ui.TextInput(
            label="Deskripsi",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=DESCRIPTION_MAX_LENGTH,
            placeholder=DESCRIPTION_PLACEHOLDER,
            default=current.get("description") or "",
        )
        self.banner_input = discord.ui.TextInput(
            label="URL Banner (opsional)",
            style=discord.TextStyle.short,
            required=False,
            max_length=500,
            placeholder="https://...",
            default=current.get("banner_url") or "",
        )
        self.footer_text_input = discord.ui.TextInput(
            label="Teks Footer",
            style=discord.TextStyle.short,
            required=False,
            max_length=FOOTER_MAX_LENGTH,
            placeholder=DEFAULT_FOOTER_TEXT,
            default=current.get("footer_text") or "",
        )
        for item in (
            self.title_input, self.description_input, self.banner_input,
            self.footer_text_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        values = {
            "title": self.title_input.value.strip(),
            "description": self.description_input.value.strip(),
            "banner_url": self.banner_input.value.strip(),
            "footer_text": self.footer_text_input.value.strip(),
        }
        await self._on_submit_callback(interaction, values)


class WelcomeCog(commands.Cog):
    """Pesan sambutan member baru + auto join-role."""

    welcome_group = app_commands.Group(
        name="welcome", description="Atur pesan sambutan member baru.", guild_only=True
    )
    joinrole_group = app_commands.Group(
        name="joinrole", description="Atur role otomatis pas ada yang gabung.", guild_only=True
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- Listener: dipicu tiap ada member (atau bot) baru gabung -------------

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self._assign_join_roles(member)
        await self._post_welcome_message(member)

    async def _assign_join_roles(self, member: discord.Member) -> None:
        runtime = RuntimeSettings(self.bot.db)
        role_ids = await (
            runtime.join_role_bot_ids(member.guild.id)
            if member.bot
            else runtime.join_role_user_ids(member.guild.id)
        )
        if not role_ids:
            return
        roles = [member.guild.get_role(rid) for rid in role_ids]
        roles = [r for r in roles if r is not None]
        if not roles:
            return
        try:
            await member.add_roles(*roles, reason="NOCTRA auto join-role")
        except discord.Forbidden:
            logger.warning(
                "Gak punya izin buat kasih auto join-role ke %s di guild %s -- cek posisi role NOCTRA.",
                member, member.guild.id,
            )
        except discord.HTTPException:
            logger.exception("Gagal kasih auto join-role ke %s.", member)

    async def _post_welcome_message(self, member: discord.Member) -> None:
        runtime = RuntimeSettings(self.bot.db)
        if not await runtime.welcome_enabled(member.guild.id):
            return
        channel_id = await runtime.welcome_channel_id(member.guild.id)
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        await self._send_welcome(member, channel)

    async def _build_container_for(self, member: discord.Member) -> discord.ui.Container:
        runtime = RuntimeSettings(self.bot.db)
        guild_id = member.guild.id
        title_template = await runtime.welcome_title(guild_id) or DEFAULT_TITLE
        description_template = await runtime.welcome_description(guild_id) or DEFAULT_DESCRIPTION
        footer_template = await runtime.welcome_footer_text(guild_id) or DEFAULT_FOOTER_TEXT
        banner_url = await runtime.welcome_banner_url(guild_id)
        color = await runtime.welcome_color(guild_id)

        return components.welcome_container(
            member,
            title=_render_template(title_template, member)[:TITLE_MAX_LENGTH] or "\u200b",
            description=_render_template(description_template, member)[:DESCRIPTION_MAX_LENGTH] or "\u200b",
            footer_text=_render_template(footer_template, member)[:FOOTER_MAX_LENGTH] or "\u200b",
            banner_url=banner_url,
            color=color if color is not None else COLOR_ACCENT,
        )

    async def _send_welcome(self, member: discord.Member, channel: discord.TextChannel) -> None:
        container = await self._build_container_for(member)
        mention_enabled = await RuntimeSettings(self.bot.db).welcome_mention_enabled(member.guild.id)
        view = components.NoctraLayout(container, timeout=None)
        # allowed_mentions ini yang BENERAN nentuin ping kejadian atau
        # enggak -- lepas dari ada/enggaknya {mention} di title/description.
        # Kalau staff gak nyantumin {mention} sama sekali di template
        # mereka, toggle /welcome mention emang gak ngefek apa-apa (gak ada
        # yang bisa di-ping), itu udah sesuai ekspektasi -- {mention} sekarang
        # placeholder biasa kayak {server}/{date}, cuma nongol kalau dipake.
        allowed = discord.AllowedMentions(users=mention_enabled, roles=False, everyone=False)
        try:
            await channel.send(view=view, allowed_mentions=allowed)
        except discord.HTTPException:
            logger.exception("Gagal posting pesan sambutan buat %s di channel %s.", member, channel.id)

    async def _save_welcome_message(self, interaction: discord.Interaction, values: dict[str, str]) -> None:
        db = self.bot.db
        guild_id = interaction.guild_id
        # String kosong SENGAJA disimpen apa adanya (bukan di-skip) --
        # itu yang bikin staff bisa "reset ke default" cukup dengan
        # ngosongin field-nya di modal (lihat RuntimeSettings.welcome_*
        # yang nganggep string kosong sama kayak belum diatur).
        await settings_q.set_setting(db, guild_scoped_key("welcome_title", guild_id), values["title"])
        await settings_q.set_setting(db, guild_scoped_key("welcome_description", guild_id), values["description"])
        await settings_q.set_setting(db, guild_scoped_key("welcome_banner_url", guild_id), values["banner_url"])
        await settings_q.set_setting(db, guild_scoped_key("welcome_footer_text", guild_id), values["footer_text"])

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=embeds.success_embed("Pesan sambutan berhasil disimpen."), ephemeral=True
            )
            return

        preview_container = await self._build_container_for(interaction.user)
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.TextDisplay(
                "Berhasil disimpen! Ini preview-nya (dirender pake akun kamu sendiri -- "
                "kalau ada `{mention}` dkk, itu bakal keganti data member asli pas beneran ada yang gabung):"
            )
        )
        view.add_item(preview_container)
        await interaction.response.send_message(view=view, ephemeral=True)

    # -- Command /welcome -----------------------------------------------------

    @welcome_group.command(name="setup", description="Atur judul/deskripsi/banner/footer pesan sambutan lewat form.")
    @staff_only()
    async def setup_message(self, interaction: discord.Interaction) -> None:
        runtime = RuntimeSettings(self.bot.db)
        guild_id = interaction.guild_id
        current = {
            "title": await runtime.welcome_title(guild_id),
            "description": await runtime.welcome_description(guild_id),
            "banner_url": await runtime.welcome_banner_url(guild_id),
            "footer_text": await runtime.welcome_footer_text(guild_id),
        }
        await interaction.response.send_modal(WelcomeMessageModal(current, self._save_welcome_message))

    @welcome_group.command(name="channel", description="Atur channel tempat pesan sambutan diposting.")
    @app_commands.describe(channel="Channel buat pesan sambutan member baru")
    @staff_only()
    async def channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await settings_q.set_setting(
            self.bot.db, guild_scoped_key("welcome_channel_id", interaction.guild_id), str(channel.id)
        )
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Channel sambutan diatur ke {channel.mention}. Pake `/welcome test` buat liat contoh hasilnya."
            ),
            ephemeral=True,
        )

    @welcome_group.command(name="toggle", description="Nyalain/matiin pesan sambutan member baru.")
    @app_commands.describe(enabled="True buat nyalain, False buat matiin")
    @staff_only()
    async def toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        await settings_q.set_setting(
            self.bot.db, guild_scoped_key("welcome_enabled", interaction.guild_id), "1" if enabled else "0"
        )
        state = "dinyalain" if enabled else "dimatiin"
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Pesan sambutan udah {state}."), ephemeral=True
        )

    @welcome_group.command(
        name="mention", description="Nyalain/matiin ping member yang baru gabung pas pesan sambutan diposting."
    )
    @app_commands.describe(enabled="True buat nge-ping member-nya, False buat diem-diem aja")
    @staff_only()
    async def mention(self, interaction: discord.Interaction, enabled: bool) -> None:
        await settings_q.set_setting(
            self.bot.db, guild_scoped_key("welcome_mention_enabled", interaction.guild_id), "1" if enabled else "0"
        )
        state = "bakal di-ping" if enabled else "gak bakal di-ping (embed doang)"
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Member yang baru gabung {state} pas pesan sambutan diposting."),
            ephemeral=True,
        )

    @welcome_group.command(name="color", description="Atur warna aksen embed sambutan (kode hex).")
    @app_commands.describe(warna="Kode warna hex, misal #7C5CFF -- kosongin buat balik ke default")
    @staff_only()
    async def color(self, interaction: discord.Interaction, warna: str | None = None) -> None:
        key = guild_scoped_key("welcome_color", interaction.guild_id)
        if not warna:
            await settings_q.set_setting(self.bot.db, key, "")
            await interaction.response.send_message(
                embed=embeds.success_embed("Warna aksen pesan sambutan dibalikin ke default."), ephemeral=True
            )
            return
        color, error = parse_hex_color(warna, COLOR_ACCENT)
        if error:
            await interaction.response.send_message(embed=embeds.error_embed(error), ephemeral=True)
            return
        await settings_q.set_setting(self.bot.db, key, str(color))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Warna aksen pesan sambutan diatur ke `#{color:06X}`."), ephemeral=True
        )

    @welcome_group.command(name="test", description="Kirim contoh pesan sambutan pake akun kamu sendiri.")
    @staff_only()
    async def test(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=embeds.error_embed("Command ini cuma bisa dipake di dalem server."), ephemeral=True
            )
            return
        channel_id = await RuntimeSettings(self.bot.db).welcome_channel_id(interaction.guild_id)
        if not channel_id:
            await interaction.response.send_message(
                embed=embeds.error_embed("Channel sambutan belum diatur. Pake `/welcome channel` dulu."),
                ephemeral=True,
            )
            return
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=embeds.error_embed("Channel sambutan gak ketemu -- mungkin udah kehapus."), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._send_welcome(interaction.user, channel)
        await interaction.followup.send(
            embed=embeds.success_embed(f"Contoh pesan sambutan udah dikirim ke {channel.mention}."), ephemeral=True
        )

    @welcome_group.command(name="view", description="Liat pengaturan pesan sambutan yang lagi aktif, plus preview.")
    @staff_only()
    async def view_settings(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=embeds.error_embed("Command ini cuma bisa dipake di dalem server."), ephemeral=True
            )
            return
        runtime = RuntimeSettings(self.bot.db)
        guild_id = interaction.guild_id
        channel_id = await runtime.welcome_channel_id(guild_id)
        summary_lines = [
            f"▸ **Status:** {'Aktif' if await runtime.welcome_enabled(guild_id) else 'Nonaktif'}",
            f"▸ **Channel:** {f'<#{channel_id}>' if channel_id else 'Belum diatur'}",
            f"▸ **Ping member:** {'Nyala' if await runtime.welcome_mention_enabled(guild_id) else 'Mati'}",
            f"▸ **Banner:** {'Diatur' if await runtime.welcome_banner_url(guild_id) else 'Belum diatur'}",
        ]
        preview_container = await self._build_container_for(interaction.user)
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.TextDisplay(
                "\n".join(summary_lines) + "\n\nPreview (dirender pake akun kamu sendiri):"
            )
        )
        view.add_item(preview_container)
        await interaction.response.send_message(view=view, ephemeral=True)

    @welcome_group.command(
        name="placeholders", description="Liat daftar placeholder yang bisa dipake di judul/deskripsi/footer."
    )
    @staff_only()
    async def placeholders(self, interaction: discord.Interaction) -> None:
        lines = [
            "`{mention}` -- mention/ping member (misal @Nama)",
            "`{user}` -- nama#tag lengkap member",
            "`{username}` -- username member",
            "`{display_name}` -- nickname member di server ini",
            "`{server}` -- nama server",
            "`{membercount}` -- jumlah member server sekarang",
            "`{date}` -- tanggal & jam gabung (otomatis nyesuain timezone tiap orang yang liat)",
            "",
            "Emoji custom server bisa langsung ditempel apa adanya di judul/deskripsi/footer -- "
            "gak perlu placeholder khusus, tinggal ketik emoji-nya kayak biasa.",
        ]
        await interaction.response.send_message(
            embed=embeds.info_embed("Placeholder Pesan Sambutan", "\n".join(lines)), ephemeral=True
        )

    # -- Command /joinrole ------------------------------------------------------

    async def _get_role_ids(self, target: str, guild_id: int) -> list[int]:
        runtime = RuntimeSettings(self.bot.db)
        return await (runtime.join_role_bot_ids(guild_id) if target == "bot" else runtime.join_role_user_ids(guild_id))

    async def _set_role_ids(self, target: str, guild_id: int, ids: list[int]) -> None:
        key = guild_scoped_key(JOIN_ROLE_SETTING_KEYS[target], guild_id)
        await settings_q.set_setting(self.bot.db, key, ",".join(str(i) for i in ids))

    @joinrole_group.command(name="add", description="Tambahin role yang otomatis kepasang pas ada yang baru gabung.")
    @app_commands.describe(
        role="Role yang mau ditambahin",
        target="Buat member biasa (user) atau bot yang ditambahin ke server?",
    )
    @staff_only()
    async def add(self, interaction: discord.Interaction, role: discord.Role, target: RoleTarget) -> None:
        if role.is_default():
            await interaction.response.send_message(
                embed=embeds.error_embed("Role `@everyone` otomatis kepasang ke semua orang, gak perlu ditambahin."),
                ephemeral=True,
            )
            return
        if role.managed:
            await interaction.response.send_message(
                embed=embeds.error_embed("Role ini dikelola integrasi/bot lain, gak bisa di-assign manual sama NOCTRA."),
                ephemeral=True,
            )
            return

        current = await self._get_role_ids(target, interaction.guild_id)
        if role.id in current:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"{role.mention} udah ada di daftar join-role buat {target}."),
                ephemeral=True,
            )
            return
        current.append(role.id)
        await self._set_role_ids(target, interaction.guild_id, current)

        warning = ""
        bot_member = interaction.guild.me if interaction.guild else None
        if bot_member and role.position >= bot_member.top_role.position:
            warning = (
                f"\n\n⚠️ Posisi role {role.mention} sejajar atau di atas role tertinggi NOCTRA di server ini -- "
                "auto-assign bisa gagal sampe posisi role NOCTRA dinaikin di **Server Settings > Roles**."
            )

        target_label = "member baru" if target == "user" else "bot baru"
        await interaction.response.send_message(
            embed=embeds.success_embed(f"{role.mention} bakal otomatis kepasang ke {target_label} yang gabung.{warning}"),
            ephemeral=True,
        )

    @joinrole_group.command(name="remove", description="Hapus role dari daftar auto join-role.")
    @app_commands.describe(role="Role yang mau dihapus", target="Dari daftar user atau bot?")
    @staff_only()
    async def remove(self, interaction: discord.Interaction, role: discord.Role, target: RoleTarget) -> None:
        current = await self._get_role_ids(target, interaction.guild_id)
        if role.id not in current:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"{role.mention} emang gak ada di daftar join-role buat {target}."),
                ephemeral=True,
            )
            return
        current.remove(role.id)
        await self._set_role_ids(target, interaction.guild_id, current)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"{role.mention} udah dihapus dari daftar join-role."), ephemeral=True
        )

    @joinrole_group.command(name="clear", description="Kosongin daftar auto join-role buat kategori tertentu.")
    @app_commands.describe(target="Kategori yang mau dikosongin")
    @staff_only()
    async def clear(self, interaction: discord.Interaction, target: ClearTarget) -> None:
        if target in ("user", "all"):
            await self._set_role_ids("user", interaction.guild_id, [])
        if target in ("bot", "all"):
            await self._set_role_ids("bot", interaction.guild_id, [])
        await interaction.response.send_message(
            embed=embeds.success_embed("Daftar join-role udah dikosongin."), ephemeral=True
        )

    @joinrole_group.command(name="list", description="Liat role yang otomatis kepasang pas ada yang gabung.")
    @staff_only()
    async def list_roles(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        user_ids = await self._get_role_ids("user", interaction.guild_id)
        bot_ids = await self._get_role_ids("bot", interaction.guild_id)

        def render(ids: list[int]) -> str:
            if not ids:
                return "Belum ada."
            lines = []
            for rid in ids:
                role = guild.get_role(rid) if guild else None
                lines.append(role.mention if role else f"`{rid}` (role udah gak ada -- bersihin pake /joinrole remove)")
            return "\n".join(lines)

        embed = embeds.info_embed(
            "Auto Join-Role",
            f"**Member biasa:**\n{render(user_ids)}\n\n**Bot:**\n{render(bot_ids)}",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WelcomeCog(bot))
