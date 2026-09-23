"""
Command staff: /boost.

Notifikasi otomatis pas ada member yang nge-boost server -- PER-SERVER
(guild_scoped_key(), pola sama persis kayak /welcome & /joinrole di
bot.cogs.welcome) soalnya tiap server yang bot ini numpang biasanya mau
gaya notif boost-nya beda-beda sendiri.

Dirender pake Components V2: thumbnail avatar BOOSTER-nya sendiri (otomatis,
gak perlu diatur staff), title + deskripsi + total boost server + footer
custom (dukung placeholder {mention}/{user}/{username}/{display_name}/
{server}/{boostcount}/{date}, dan emoji custom server -- tinggal ketik
langsung di title/deskripsi/footer manapun, sama kayak /welcome, Discord
yang render otomatis).

Trigger: listener on_member_update, dipicu KHUSUS pas premium_since member
itu berubah dari None -> ada isinya (boost baru), BUKAN tiap kali Discord
ngirim update member yang udah lama boost (biar gak nge-spam notif
berkali-kali buat orang yang sama).
"""

from __future__ import annotations

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

DEFAULT_TITLE = "\U0001F680 Boost Baru!"
DEFAULT_DESCRIPTION = (
    "Makasih banyak {mention} udah nge-boost **{server}**! \U0001F49C\n\n"
    "Kontribusi kamu bikin server ini makin keren."
)
DEFAULT_FOOTER_TEXT = "{server}"

# Placeholder khusus buat modal -- placeholder field Modal Discord dibatasin
# maksimal 100 karakter (beda sama max_length value yang boleh sampe 4000),
# makanya dipisah dari DEFAULT_DESCRIPTION yang sengaja lebih panjang
# karena dipake juga sebagai fallback isi notifikasi beneran.
DESCRIPTION_PLACEHOLDER = "Makasih {mention} udah boost {server}! (dukung {boostcount}, {date}, dst.)"

TITLE_MAX_LENGTH = 256
DESCRIPTION_MAX_LENGTH = 4000
FOOTER_MAX_LENGTH = 2048


def _render_template(template: str, member: discord.Member) -> str:
    """Ganti placeholder di template jadi data booster/server yang beneran.
    Emoji custom server gak butuh apa-apa di sini -- staff tinggal ketik
    langsung kodenya (misal <:sukses:123...>) di title/description/footer,
    Discord yang render otomatis asal bot-nya juga ada di server yang sama
    dengan emoji itu."""
    guild = member.guild
    boosted_at = member.premium_since or discord.utils.utcnow()
    boosted_ts = int(boosted_at.timestamp())
    replacements = {
        "{mention}": member.mention,
        "{user}": str(member),
        "{username}": member.name,
        "{display_name}": member.display_name,
        "{server}": guild.name,
        "{boostcount}": f"{guild.premium_subscription_count:,}",
        "{date}": f"<t:{boosted_ts}:F>",
    }
    rendered = template
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    return rendered


class BoostMessageModal(discord.ui.Modal, title="Atur Notifikasi Boost"):
    """4 field (batas maksimal Modal Discord yang nyaman dipakein sekaligus)
    buat semua bagian teks notifikasi boost yang bisa dikustom. Nilai yang
    lagi aktif di-prefill biar staff gak perlu ngetik ulang dari nol."""

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


class BoostCog(commands.Cog):
    """Notifikasi otomatis pas ada member yang nge-boost server."""

    boost_group = app_commands.Group(
        name="boost", description="Atur notifikasi server boost.", guild_only=True
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- Listener: dipicu tiap ada member yang BARU mulai boost -------------

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if before.premium_since is None and after.premium_since is not None:
            await self._post_boost_message(after)

    async def _post_boost_message(self, member: discord.Member) -> None:
        runtime = RuntimeSettings(self.bot.db)
        if not await runtime.boost_enabled(member.guild.id):
            return
        channel_id = await runtime.boost_channel_id(member.guild.id)
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        await self._send_boost(member, channel)

    async def _build_container_for(self, member: discord.Member) -> discord.ui.Container:
        runtime = RuntimeSettings(self.bot.db)
        guild_id = member.guild.id
        title_template = await runtime.boost_title(guild_id) or DEFAULT_TITLE
        description_template = await runtime.boost_description(guild_id) or DEFAULT_DESCRIPTION
        footer_template = await runtime.boost_footer_text(guild_id) or DEFAULT_FOOTER_TEXT
        banner_url = await runtime.boost_banner_url(guild_id)
        color = await runtime.boost_color(guild_id)

        return components.boost_container(
            member,
            title=_render_template(title_template, member)[:TITLE_MAX_LENGTH] or "\u200b",
            description=_render_template(description_template, member)[:DESCRIPTION_MAX_LENGTH] or "\u200b",
            footer_text=_render_template(footer_template, member)[:FOOTER_MAX_LENGTH] or "\u200b",
            total_boosts=member.guild.premium_subscription_count,
            banner_url=banner_url,
            color=color if color is not None else COLOR_ACCENT,
        )

    async def _send_boost(self, member: discord.Member, channel: discord.TextChannel) -> None:
        container = await self._build_container_for(member)
        mention_enabled = await RuntimeSettings(self.bot.db).boost_mention_enabled(member.guild.id)
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(container)
        # allowed_mentions ini yang beneran nentuin ping kejadian atau enggak
        # -- lepas dari ada/enggaknya {mention} di title/description, sama
        # persis alasannya kayak _send_welcome() di bot.cogs.welcome.
        allowed = discord.AllowedMentions(users=mention_enabled, roles=False, everyone=False)
        try:
            await channel.send(view=view, allowed_mentions=allowed)
        except discord.HTTPException:
            logger.exception("Gagal posting notifikasi boost buat %s di channel %s.", member, channel.id)

    async def _save_boost_message(self, interaction: discord.Interaction, values: dict[str, str]) -> None:
        db = self.bot.db
        guild_id = interaction.guild_id
        # String kosong SENGAJA disimpen apa adanya (bukan di-skip) -- itu
        # yang bikin staff bisa "reset ke default" cukup dengan ngosongin
        # field-nya di modal.
        await settings_q.set_setting(db, guild_scoped_key("boost_title", guild_id), values["title"])
        await settings_q.set_setting(db, guild_scoped_key("boost_description", guild_id), values["description"])
        await settings_q.set_setting(db, guild_scoped_key("boost_banner_url", guild_id), values["banner_url"])
        await settings_q.set_setting(db, guild_scoped_key("boost_footer_text", guild_id), values["footer_text"])

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=embeds.success_embed("Notifikasi boost berhasil disimpen."), ephemeral=True
            )
            return

        preview_container = await self._build_container_for(interaction.user)
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.TextDisplay(
                "Berhasil disimpen! Ini preview-nya (dirender pake akun & avatar kamu sendiri -- "
                "total boost yang dipake tetep angka ASLI server ini):"
            )
        )
        view.add_item(preview_container)
        await interaction.response.send_message(view=view, ephemeral=True)

    # -- Command /boost -----------------------------------------------------

    @boost_group.command(name="setup", description="Atur judul/deskripsi/banner/footer notifikasi boost lewat form.")
    @staff_only()
    async def setup_message(self, interaction: discord.Interaction) -> None:
        runtime = RuntimeSettings(self.bot.db)
        guild_id = interaction.guild_id
        current = {
            "title": await runtime.boost_title(guild_id),
            "description": await runtime.boost_description(guild_id),
            "banner_url": await runtime.boost_banner_url(guild_id),
            "footer_text": await runtime.boost_footer_text(guild_id),
        }
        await interaction.response.send_modal(BoostMessageModal(current, self._save_boost_message))

    @boost_group.command(name="channel", description="Atur channel tempat notifikasi boost diposting.")
    @app_commands.describe(channel="Channel buat notifikasi boost")
    @staff_only()
    async def channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await settings_q.set_setting(
            self.bot.db, guild_scoped_key("boost_channel_id", interaction.guild_id), str(channel.id)
        )
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Channel notifikasi boost diatur ke {channel.mention}. "
                "Pake `/boost test` buat liat contoh hasilnya."
            ),
            ephemeral=True,
        )

    @boost_group.command(name="toggle", description="Nyalain/matiin notifikasi server boost.")
    @app_commands.describe(enabled="True buat nyalain, False buat matiin")
    @staff_only()
    async def toggle(self, interaction: discord.Interaction, enabled: bool) -> None:
        await settings_q.set_setting(
            self.bot.db, guild_scoped_key("boost_enabled", interaction.guild_id), "1" if enabled else "0"
        )
        state = "dinyalain" if enabled else "dimatiin"
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Notifikasi boost udah {state}."), ephemeral=True
        )

    @boost_group.command(name="mention", description="Nyalain/matiin ping booster pas notifikasi diposting.")
    @app_commands.describe(enabled="True buat nge-ping booster-nya, False buat diem-diem aja")
    @staff_only()
    async def mention(self, interaction: discord.Interaction, enabled: bool) -> None:
        await settings_q.set_setting(
            self.bot.db, guild_scoped_key("boost_mention_enabled", interaction.guild_id), "1" if enabled else "0"
        )
        state = "bakal di-ping" if enabled else "gak bakal di-ping (kartu doang)"
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Booster {state} pas notifikasi boost diposting."), ephemeral=True
        )

    @boost_group.command(name="color", description="Atur warna aksen kartu notifikasi boost (kode hex).")
    @app_commands.describe(warna="Kode warna hex, misal #F47FFF -- kosongin buat balik ke default")
    @staff_only()
    async def color(self, interaction: discord.Interaction, warna: str | None = None) -> None:
        key = guild_scoped_key("boost_color", interaction.guild_id)
        if not warna:
            await settings_q.set_setting(self.bot.db, key, "")
            await interaction.response.send_message(
                embed=embeds.success_embed("Warna aksen notifikasi boost dibalikin ke default."), ephemeral=True
            )
            return
        color, error = parse_hex_color(warna, COLOR_ACCENT)
        if error:
            await interaction.response.send_message(embed=embeds.error_embed(error), ephemeral=True)
            return
        await settings_q.set_setting(self.bot.db, key, str(color))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Warna aksen notifikasi boost diatur ke `#{color:06X}`."), ephemeral=True
        )

    @boost_group.command(name="test", description="Kirim contoh notifikasi boost pake akun & avatar kamu sendiri.")
    @staff_only()
    async def test(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=embeds.error_embed("Command ini cuma bisa dipake di dalem server."), ephemeral=True
            )
            return
        channel_id = await RuntimeSettings(self.bot.db).boost_channel_id(interaction.guild_id)
        if not channel_id:
            await interaction.response.send_message(
                embed=embeds.error_embed("Channel notifikasi boost belum diatur. Pake `/boost channel` dulu."),
                ephemeral=True,
            )
            return
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=embeds.error_embed("Channel notifikasi boost gak ketemu -- mungkin udah kehapus."),
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await self._send_boost(interaction.user, channel)
        await interaction.followup.send(
            embed=embeds.success_embed(f"Contoh notifikasi boost udah dikirim ke {channel.mention}."),
            ephemeral=True,
        )

    @boost_group.command(name="view", description="Liat pengaturan notifikasi boost yang lagi aktif, plus preview.")
    @staff_only()
    async def view_settings(self, interaction: discord.Interaction) -> None:
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                embed=embeds.error_embed("Command ini cuma bisa dipake di dalem server."), ephemeral=True
            )
            return
        runtime = RuntimeSettings(self.bot.db)
        guild_id = interaction.guild_id
        channel_id = await runtime.boost_channel_id(guild_id)
        summary_lines = [
            f"▸ **Status:** {'Aktif' if await runtime.boost_enabled(guild_id) else 'Nonaktif'}",
            f"▸ **Channel:** {f'<#{channel_id}>' if channel_id else 'Belum diatur'}",
            f"▸ **Ping booster:** {'Nyala' if await runtime.boost_mention_enabled(guild_id) else 'Mati'}",
            f"▸ **Banner:** {'Diatur' if await runtime.boost_banner_url(guild_id) else 'Belum diatur'}",
            f"▸ **Total boost server sekarang:** {interaction.guild.premium_subscription_count:,}",
        ]
        preview_container = await self._build_container_for(interaction.user)
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(
            discord.ui.TextDisplay(
                "\n".join(summary_lines) + "\n\nPreview (dirender pake akun & avatar kamu sendiri):"
            )
        )
        view.add_item(preview_container)
        await interaction.response.send_message(view=view, ephemeral=True)

    @boost_group.command(
        name="placeholders", description="Liat daftar placeholder yang bisa dipake di judul/deskripsi/footer."
    )
    @staff_only()
    async def placeholders(self, interaction: discord.Interaction) -> None:
        lines = [
            "`{mention}` -- mention/ping booster (misal @Nama)",
            "`{user}` -- nama#tag lengkap booster",
            "`{username}` -- username booster",
            "`{display_name}` -- nickname booster di server ini",
            "`{server}` -- nama server",
            "`{boostcount}` -- total boost server sekarang",
            "`{date}` -- tanggal & jam boost (otomatis nyesuain timezone tiap orang yang liat)",
            "",
            "Emoji custom server bisa langsung ditempel apa adanya di judul/deskripsi/footer -- "
            "gak perlu placeholder khusus, tinggal ketik emoji-nya kayak biasa.",
        ]
        await interaction.response.send_message(
            embed=embeds.info_embed("Placeholder Notifikasi Boost", "\n".join(lines)), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BoostCog(bot))
