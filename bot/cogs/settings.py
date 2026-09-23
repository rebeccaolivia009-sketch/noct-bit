"""Command admin: /settings"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from bot.database.queries import settings as settings_q
from bot.ui import embeds
from bot.ui.views import CardPanelView, ShopPanelView
from bot.utils import activity_log
from bot.utils.helpers import RuntimeSettings
from bot.utils.leaderboard import refresh_leaderboard
from bot.utils.permissions import staff_only
from bot.utils.validators import is_valid_emoji


class SettingsCog(commands.Cog):
    """Atur staff role, kategori/channel ticket, mata uang, dan auto-archive.

    Command dikelompokin jadi subgroup per fitur (/settings review, /settings
    order, /settings ticket, /settings leaderboard, /settings card) -- BUKAN
    semuanya rata di /settings langsung -- soalnya Discord ngebatesin maksimal
    25 subcommand per grup, dan udah pernah literally kena limit itu waktu
    semuanya numpuk jadi satu (lihat git history: bot.cogs.settings sempet
    gagal ke-load total gara-gara ValueError: maximum number of child
    commands exceeded). Subgroup juga bikin command lebih gampang ditemuin
    (semua soal review nyatu di satu tempat, dst)."""

    settings_group = app_commands.Group(
        name="settings", description="Atur NOCTRA.", guild_only=True
    )
    card_group = app_commands.Group(
        name="card", description="Atur fitur kartu digital NOCTRA.", parent=settings_group
    )
    review_group = app_commands.Group(
        name="review", description="Atur kartu review publik & notifikasi bukti review.", parent=settings_group
    )
    order_group = app_commands.Group(
        name="order", description="Atur channel order-log & pengumuman pembelian.", parent=settings_group
    )
    ticket_group = app_commands.Group(
        name="ticket", description="Atur kategori, log channel, dan auto-archive ticket.", parent=settings_group
    )
    leaderboard_group = app_commands.Group(
        name="leaderboard", description="Atur channel dan visibilitas leaderboard Top Spenders.", parent=settings_group
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- Top-level (berdiri sendiri, gak masuk subgroup manapun) ---------------

    @settings_group.command(name="shop_panel", description="Posting panel tombol Browse Store di channel ini.")
    @app_commands.describe(
        title="Judul panel",
        description="Isi teks panel",
        image_url="Gambar banner full-width di bawah teks (PNG/JPG/WebP)",
        thumbnail_url="Logo/thumbnail kecil di kanan atas (PNG/JPG/WebP)",
        button_label="Teks yang muncul di tombol",
    )
    @staff_only()
    async def shop_panel(
        self,
        interaction: discord.Interaction,
        title: str = "NOCTRA STORE",
        description: str = "Klik di bawah buat jelajahin katalog dan pesen -- gak perlu command.",
        image_url: str | None = None,
        thumbnail_url: str | None = None,
        button_label: str = "Jelajahi Toko",
    ) -> None:
        view = ShopPanelView(
            title=title, description=description, image_url=image_url,
            thumbnail_url=thumbnail_url, button_label=button_label,
        )
        await interaction.channel.send(view=view)
        await interaction.response.send_message(embed=embeds.success_embed("Panel toko udah diposting."), ephemeral=True)

    @settings_group.command(
        name="activity_log_channel",
        description="Atur channel log aktivitas staff (settings, order, kartu, moderasi review).",
    )
    @app_commands.describe(channel="Channel internal buat log aktivitas staff")
    @staff_only()
    async def activity_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await settings_q.set_setting(self.bot.db, "activity_log_channel_id", str(channel.id))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Channel log aktivitas diatur ke {channel.mention}."), ephemeral=True
        )

    @settings_group.command(
        name="ad_channel",
        description="Atur channel default buat /iklan kalau parameter channel-nya gak diisi.",
    )
    @app_commands.describe(channel="Channel default buat posting iklan")
    @staff_only()
    async def ad_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await settings_q.set_setting(self.bot.db, "ad_channel_id", str(channel.id))
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Channel default iklan diatur ke {channel.mention}. "
                "Pake `/iklan` kapan aja -- kalau parameter `channel`-nya gak diisi, otomatis kesitu."
            ),
            ephemeral=True,
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah", f"Channel default iklan diatur ke {channel.mention}."
        )

    @settings_group.command(
        name="main_server_invite",
        description="Atur link invite server utama -- muncul jadi tombol 'Gabung Server' abis customer selesai review.",
    )
    @app_commands.describe(invite_url="Link invite Discord server utama kamu, contoh https://discord.gg/xxxxx")
    @staff_only()
    async def main_server_invite(self, interaction: discord.Interaction, invite_url: str) -> None:
        await settings_q.set_setting(self.bot.db, "main_server_invite_url", invite_url)
        await interaction.response.send_message(
            embed=embeds.success_embed("Link invite server utama udah diatur."), ephemeral=True
        )
        await activity_log.log_activity(self.bot, interaction.user, "Setting Diubah", "Link invite server utama diubah.")

    @settings_group.command(name="staff_role", description="Atur role staff buat command admin dan ticket.")
    @app_commands.describe(role="Role yang bakal dianggep staff")
    @staff_only()
    async def staff_role(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await settings_q.set_setting(self.bot.db, "staff_role_id", str(role.id))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Role staff diatur ke {role.mention}."), ephemeral=True
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah", f"Role staff diatur ke {role.mention}."
        )

    @settings_group.command(name="currency", description="Atur label mata uang default buat produk baru.")
    @app_commands.describe(currency_label="contoh: USD, IDR, Robux")
    @staff_only()
    async def currency(self, interaction: discord.Interaction, currency_label: str) -> None:
        await settings_q.set_setting(self.bot.db, "default_currency", currency_label)
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Mata uang default diatur ke **{currency_label}**."), ephemeral=True
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah", f"Mata uang default diatur ke **{currency_label}**."
        )

    @settings_group.command(name="view", description="Liat pengaturan yang lagi aktif.")
    @staff_only()
    async def view(self, interaction: discord.Interaction) -> None:
        runtime = RuntimeSettings(self.bot.db)
        excluded_count = len(await runtime.leaderboard_excluded_user_ids())
        values = {
            "staff_role_id": await runtime.staff_role_id(),
            "order_log_channel_id": await runtime.order_log_channel_id(),
            "reviews_channel_id": await runtime.reviews_channel_id(),
            "testi_proof_channel_id": await runtime.testi_proof_channel_id(),
            "activity_log_channel_id": await runtime.activity_log_channel_id(),
            "card_requests_channel_id": await runtime.card_requests_channel_id(),
            "card_admin_fee": await runtime.card_admin_fee(),
            "card_noctoin_rate": await runtime.card_noctoin_rate(),
            "purchase_feed_channel_id": await runtime.purchase_feed_channel_id(),
            "ad_channel_id": await runtime.ad_channel_id(),
            "main_server_invite_url": await runtime.main_server_invite_url(),
            "review_banner_url": await runtime.review_banner_url(),
            "leaderboard_channel_id": await runtime.leaderboard_channel_id(),
            "leaderboard_excluded_users": excluded_count or "Gak ada",
            "ticket_category_id": await runtime.ticket_category_id(),
            "ticket_archive_category_id": await runtime.ticket_archive_category_id(),
            "ticket_log_channel_id": await runtime.ticket_log_channel_id(),
            "ticket_auto_archive_hours": await runtime.ticket_auto_archive_hours(),
            "default_currency": await runtime.default_currency(),
        }
        await interaction.response.send_message(embed=embeds.settings_embed(values), ephemeral=True)

    # -- /settings card ---------------------------------------------------------

    @card_group.command(name="panel", description="Posting panel Buat Kartu/Isi Saldo/Cek Saldo di channel ini.")
    @app_commands.describe(
        title="Judul panel",
        description="Isi teks panel",
    )
    @staff_only()
    async def card_panel(
        self,
        interaction: discord.Interaction,
        title: str = "Kartu Digital NOCTRA",
        description: str = "Punya kartu buat belanja lebih gampang -- gak perlu transfer ulang tiap order.",
    ) -> None:
        view = CardPanelView(title=title, description=description)
        await interaction.channel.send(view=view)
        await interaction.response.send_message(
            embed=embeds.success_embed("Panel kartu udah diposting."), ephemeral=True
        )

    @card_group.command(
        name="requests_channel",
        description="Atur channel tempat permintaan kartu/isi saldo diteruskan buat staff approve.",
    )
    @app_commands.describe(channel="Channel staff buat approve/reject permintaan kartu")
    @staff_only()
    async def card_requests_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await settings_q.set_setting(self.bot.db, "card_requests_channel_id", str(channel.id))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Channel permintaan kartu diatur ke {channel.mention}."), ephemeral=True
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah",
            f"Channel permintaan kartu diatur ke {channel.mention}.",
        )

    @card_group.command(name="admin_fee", description="Atur biaya admin pembuatan kartu (nominal tetap).")
    @app_commands.describe(fee="Nominal biaya admin, misal 5000 -- dipotong sekali doang pas kartu dibuat")
    @staff_only()
    async def card_admin_fee(self, interaction: discord.Interaction, fee: app_commands.Range[float, 0, None]) -> None:
        await settings_q.set_setting(self.bot.db, "card_admin_fee", str(fee))
        currency = await RuntimeSettings(self.bot.db).default_currency()
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Biaya admin pembuatan kartu diatur ke {fee:,.0f} {currency}."),
            ephemeral=True,
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah", f"Biaya admin pembuatan kartu diatur ke {fee:,.0f} {currency}."
        )

    @card_group.command(
        name="noctoin_rate",
        description="Atur rate Noctoins -- berapa rupiah buat dapet 1 (sekaligus buat potongan 1 Noctoin).",
    )
    @app_commands.describe(rate="Nominal per 1 Noctoin, misal 10000 (belanja 10rb=1 Noctoin, 1 Noctoin=potongan 10rb)")
    @staff_only()
    async def card_noctoin_rate(self, interaction: discord.Interaction, rate: app_commands.Range[float, 1, None]) -> None:
        await settings_q.set_setting(self.bot.db, "card_noctoin_rate", str(rate))
        currency = await RuntimeSettings(self.bot.db).default_currency()
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Rate Noctoins diatur ke {rate:,.0f} {currency} per 1 Noctoin (dua arah -- dapet & potongan)."
            ),
            ephemeral=True,
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah", f"Rate Noctoins diatur ke {rate:,.0f} {currency} per 1 Noctoin."
        )

    # -- /settings review ---------------------------------------------------------

    @review_group.command(
        name="channel",
        description="Atur channel publik tempat review customer yang di-approve diposting (buat reputasi toko).",
    )
    @app_commands.describe(channel="Channel publik buat showcase review")
    @staff_only()
    async def review_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await settings_q.set_setting(self.bot.db, "reviews_channel_id", str(channel.id))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Channel review diatur ke {channel.mention}."), ephemeral=True
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah", f"Channel review publik diatur ke {channel.mention}."
        )

    @review_group.command(
        name="testi_channel",
        description="Atur channel notif internal begitu customer kirim foto bukti review (beda dari review channel).",
    )
    @app_commands.describe(channel="Channel internal staff buat notifikasi bukti review")
    @staff_only()
    async def testi_proof_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await settings_q.set_setting(self.bot.db, "testi_proof_channel_id", str(channel.id))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Channel notifikasi bukti review diatur ke {channel.mention}."),
            ephemeral=True,
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah", f"Channel notifikasi bukti review diatur ke {channel.mention}."
        )

    async def _update_emoji_settings(
        self, interaction: discord.Interaction, updates: dict[str, str | None], *, log_title: str
    ) -> bool:
        """Helper bareng buat review_emoji & testi_proof_emoji -- validasi
        semua parameter yang diisi dulu SEBELUM nyimpen apapun, biar gak
        ada kondisi setengah update kalau salah satu emoji-nya invalid.
        Return True kalau berhasil disimpen (caller kirim pesan sukses),
        False kalau ada yang invalid (pesan error udah dikirim di sini)."""
        for key, value in updates.items():
            if value is not None and not is_valid_emoji(value):
                await interaction.response.send_message(
                    embed=embeds.error_embed(f"`{value}` bukan emoji yang valid."), ephemeral=True
                )
                return False
        changed = {k: v for k, v in updates.items() if v is not None}
        for key, value in changed.items():
            await settings_q.set_setting(self.bot.db, key, value)
        if changed:
            await activity_log.log_activity(
                self.bot, interaction.user, log_title,
                "\n".join(f"`{k}` -> {v}" for k, v in changed.items()),
            )
        return True

    @review_group.command(
        name="emoji",
        description="Atur emoji custom buat kartu review publik. Parameter yang gak diisi tetep kepake yang lama.",
    )
    @app_commands.describe(
        title="Emoji di judul kartu review",
        user="Emoji di baris User",
        product="Emoji di baris Product",
        rating="Emoji di baris Rating (icon-nya, beda dari bintang isi/kosong)",
        star_filled="Emoji buat bintang terisi di Rating",
        star_empty="Emoji buat bintang kosong di Rating",
        message="Emoji di baris Pesan",
    )
    @staff_only()
    async def review_emoji(
        self,
        interaction: discord.Interaction,
        title: str | None = None,
        user: str | None = None,
        product: str | None = None,
        rating: str | None = None,
        star_filled: str | None = None,
        star_empty: str | None = None,
        message: str | None = None,
    ) -> None:
        ok = await self._update_emoji_settings(
            interaction,
            {
                "review_card_emoji_title": title,
                "review_card_emoji_user": user,
                "review_card_emoji_product": product,
                "review_card_emoji_rating": rating,
                "review_card_emoji_star_filled": star_filled,
                "review_card_emoji_star_empty": star_empty,
                "review_card_emoji_message": message,
            },
            log_title="Emoji Kartu Review Diubah",
        )
        if ok:
            await interaction.response.send_message(
                embed=embeds.success_embed("Emoji kartu review udah diupdate."), ephemeral=True
            )

    @review_group.command(
        name="testi_emoji",
        description="Atur emoji custom buat notifikasi bukti review. Parameter yang gak diisi tetep kepake yang lama.",
    )
    @app_commands.describe(
        title="Emoji di judul notifikasi",
        buyer="Emoji di baris Buyer",
        product="Emoji di baris Product",
        price="Emoji di baris Price",
        testi="Emoji di baris Testi #",
    )
    @staff_only()
    async def testi_proof_emoji(
        self,
        interaction: discord.Interaction,
        title: str | None = None,
        buyer: str | None = None,
        product: str | None = None,
        price: str | None = None,
        testi: str | None = None,
    ) -> None:
        ok = await self._update_emoji_settings(
            interaction,
            {
                "testi_proof_emoji_title": title,
                "testi_proof_emoji_buyer": buyer,
                "testi_proof_emoji_product": product,
                "testi_proof_emoji_price": price,
                "testi_proof_emoji_testi": testi,
            },
            log_title="Emoji Notif Bukti Review Diubah",
        )
        if ok:
            await interaction.response.send_message(
                embed=embeds.success_embed("Emoji notifikasi bukti review udah diupdate."), ephemeral=True
            )

    @review_group.command(
        name="banner",
        description="Atur gambar banner default buat kartu review yang customer-nya gak nyertain foto.",
    )
    @app_commands.describe(image_url="URL gambar banner default (PNG/JPG/WebP)")
    @staff_only()
    async def review_banner(self, interaction: discord.Interaction, image_url: str) -> None:
        await settings_q.set_setting(self.bot.db, "review_banner_url", image_url)
        await interaction.response.send_message(
            embed=embeds.success_embed("Banner default buat review udah diatur.").set_thumbnail(url=image_url),
            ephemeral=True,
        )
        await activity_log.log_activity(self.bot, interaction.user, "Setting Diubah", "Banner default review diubah.")

    # -- /settings order ---------------------------------------------------------

    @order_group.command(
        name="log_channel",
        description="Atur channel tempat order baru diposting sama kontrol staff (Mark Paid/Completed/Cancel/Refund).",
    )
    @app_commands.describe(channel="Channel buat notifikasi order dan kontrol staff")
    @staff_only()
    async def order_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await settings_q.set_setting(self.bot.db, "order_log_channel_id", str(channel.id))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Channel order-log diatur ke {channel.mention}."), ephemeral=True
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah", f"Channel order-log diatur ke {channel.mention}."
        )

    @order_group.command(
        name="purchase_feed",
        description="Atur channel tempat pengumuman 'Si X baru aja beli Y' diposting.",
    )
    @app_commands.describe(channel="Channel publik buat pengumuman pembelian")
    @staff_only()
    async def purchase_feed_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await settings_q.set_setting(self.bot.db, "purchase_feed_channel_id", str(channel.id))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Pengumuman pembelian bakal diposting di {channel.mention}."),
            ephemeral=True,
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah", f"Channel pengumuman pembelian diatur ke {channel.mention}."
        )

    # -- /settings ticket ---------------------------------------------------------

    @ticket_group.command(name="category", description="Atur kategori tempat channel ticket baru dibuat.")
    @app_commands.describe(category="Category channel buat ticket baru")
    @staff_only()
    async def ticket_category(self, interaction: discord.Interaction, category: discord.CategoryChannel) -> None:
        await settings_q.set_setting(self.bot.db, "ticket_category_id", str(category.id))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Kategori ticket diatur ke **{category.name}**."), ephemeral=True
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah", f"Kategori ticket diatur ke **{category.name}**."
        )

    @ticket_group.command(name="archive_category", description="Atur kategori tempat ticket yang di-auto-archive dipindahin.")
    @app_commands.describe(category="Category channel buat ticket yang diarsipin")
    @staff_only()
    async def archive_category(self, interaction: discord.Interaction, category: discord.CategoryChannel) -> None:
        await settings_q.set_setting(self.bot.db, "ticket_archive_category_id", str(category.id))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Kategori archive diatur ke **{category.name}**."), ephemeral=True
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah", f"Kategori archive ticket diatur ke **{category.name}**."
        )

    @ticket_group.command(name="log_channel", description="Atur channel tempat transcript ticket diposting.")
    @app_commands.describe(channel="Channel buat transcript dan log ticket")
    @staff_only()
    async def ticket_log_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await settings_q.set_setting(self.bot.db, "ticket_log_channel_id", str(channel.id))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Log channel diatur ke {channel.mention}."), ephemeral=True
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah", f"Channel transcript ticket diatur ke {channel.mention}."
        )

    @ticket_group.command(name="auto_archive_hours", description="Jam inaktif sebelum ticket di-auto-archive.")
    @app_commands.describe(hours="Jumlah jam")
    @staff_only()
    async def ticket_auto_archive_hours(
        self, interaction: discord.Interaction, hours: app_commands.Range[int, 1, 720]
    ) -> None:
        await settings_q.set_setting(self.bot.db, "ticket_auto_archive_hours", str(hours))
        await interaction.response.send_message(
            embed=embeds.success_embed(f"Ticket bakal auto-archive abis {hours} jam gak ada aktivitas."),
            ephemeral=True,
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah", f"Auto-archive ticket diatur ke {hours} jam."
        )

    # -- /settings leaderboard ---------------------------------------------------------

    @leaderboard_group.command(name="channel", description="Atur channel buat gambar leaderboard Top Spenders.")
    @app_commands.describe(channel="Channel khusus buat gambar leaderboard")
    @staff_only()
    async def leaderboard_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await settings_q.set_setting(self.bot.db, "leaderboard_channel_id", str(channel.id))
        await settings_q.set_setting(self.bot.db, "leaderboard_message_id", "")
        await interaction.response.send_message(
            embed=embeds.success_embed(
                f"Channel leaderboard diatur ke {channel.mention}. "
                "Pake `/settings leaderboard refresh` buat posting gambar pertamanya."
            ),
            ephemeral=True,
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah", f"Channel leaderboard diatur ke {channel.mention}."
        )

    @leaderboard_group.command(name="refresh", description="Posting atau refresh gambar leaderboard manual sekarang juga.")
    @staff_only()
    async def leaderboard_refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok = await refresh_leaderboard(self.bot)
        if ok:
            await interaction.followup.send(embed=embeds.success_embed("Leaderboard udah di-refresh."), ephemeral=True)
        else:
            await interaction.followup.send(
                embed=embeds.error_embed(
                    "Gak bisa refresh leaderboard. Pastiin "
                    "`/settings leaderboard channel` udah diatur dan ada minimal satu order yang selesai."
                ),
                ephemeral=True,
            )

    @leaderboard_group.command(
        name="exclude",
        description="Sembunyiin spend user dari leaderboard Top Spenders (misal akun tester).",
    )
    @app_commands.describe(user="User yang mau disembunyiin dari leaderboard")
    @staff_only()
    async def leaderboard_exclude(self, interaction: discord.Interaction, user: discord.User) -> None:
        runtime = RuntimeSettings(self.bot.db)
        excluded = await runtime.leaderboard_excluded_user_ids()
        if user.id in excluded:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"{user.mention} udah disembunyiin dari leaderboard."),
                ephemeral=True,
            )
            return
        excluded.append(user.id)
        await settings_q.set_setting(
            self.bot.db, "leaderboard_excluded_users", ",".join(str(uid) for uid in excluded)
        )
        await interaction.response.defer(ephemeral=True)
        await refresh_leaderboard(self.bot)
        await interaction.followup.send(
            embed=embeds.success_embed(
                f"{user.mention} sekarang disembunyiin dari leaderboard. Order lama mereka gak diapa-apain -- "
                "cuma gak kehitung di sini aja. Leaderboard udah di-refresh."
            ),
            ephemeral=True,
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah", f"{user.mention} disembunyiin dari leaderboard."
        )

    @leaderboard_group.command(
        name="include",
        description="Balikin lagi user yang sebelumnya disembunyiin dari leaderboard.",
    )
    @app_commands.describe(user="User yang mau dimasukin lagi ke leaderboard")
    @staff_only()
    async def leaderboard_include(self, interaction: discord.Interaction, user: discord.User) -> None:
        runtime = RuntimeSettings(self.bot.db)
        excluded = await runtime.leaderboard_excluded_user_ids()
        if user.id not in excluded:
            await interaction.response.send_message(
                embed=embeds.error_embed(f"{user.mention} lagi gak disembunyiin kok."), ephemeral=True
            )
            return
        excluded.remove(user.id)
        await settings_q.set_setting(
            self.bot.db, "leaderboard_excluded_users", ",".join(str(uid) for uid in excluded)
        )
        await interaction.response.defer(ephemeral=True)
        await refresh_leaderboard(self.bot)
        await interaction.followup.send(
            embed=embeds.success_embed(f"{user.mention} udah gak disembunyiin lagi. Leaderboard udah di-refresh."),
            ephemeral=True,
        )
        await activity_log.log_activity(
            self.bot, interaction.user, "Setting Diubah", f"{user.mention} dimasukin lagi ke leaderboard."
        )

    @leaderboard_group.command(
        name="list",
        description="Liat daftar user yang lagi disembunyiin dari leaderboard.",
    )
    @staff_only()
    async def leaderboard_excluded_list(self, interaction: discord.Interaction) -> None:
        runtime = RuntimeSettings(self.bot.db)
        excluded = await runtime.leaderboard_excluded_user_ids()
        if not excluded:
            await interaction.response.send_message(
                embed=embeds.info_embed("Disembunyiin dari Leaderboard", "Belum ada user yang disembunyiin."),
                ephemeral=True,
            )
            return
        lines = "\n".join(f"<@{uid}> (`{uid}`)" for uid in excluded)
        await interaction.response.send_message(
            embed=embeds.info_embed("Disembunyiin dari Leaderboard", lines), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SettingsCog(bot))
