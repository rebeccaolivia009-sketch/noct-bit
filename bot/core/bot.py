"""The NOCTRA bot client: wires together the database, cogs, persistent
views, and command tree sync.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from bot.core.config import config
from bot.core.errors import setup_error_handler
from bot.core.logger import logger
from bot.database.core import Database

EXTENSIONS = (
    "bot.cogs.category",
    "bot.cogs.category_type",
    "bot.cogs.product",
    "bot.cogs.payment",
    "bot.cogs.settings",
    "bot.cogs.shop",
    "bot.cogs.order",
    "bot.cogs.ticket",
    "bot.cogs.review",
    "bot.cogs.review_photo",
    "bot.cogs.payment_proof",
    "bot.cogs.tasks",
    "bot.cogs.panel",
    "bot.cogs.announcement",
    "bot.cogs.store_status",
    "bot.cogs.card",
    "bot.cogs.advertisement",
    "bot.cogs.welcome",
    "bot.cogs.backup",
    "bot.cogs.boost",
    "bot.cogs.giveaway",
    "bot.cogs.shipment",
    "bot.cogs.badge",
    "bot.cogs.invite_tracker",
    "bot.cogs.roblox_listing",
)


class NoctraBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True  # needed to reliably manage ticket channel permissions
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.db = Database(config.database_path)

    async def setup_hook(self) -> None:
        await self.db.connect()
        await self.db.init_schema()

        for extension in EXTENSIONS:
            try:
                await self.load_extension(extension)
                logger.info("Loaded extension: %s", extension)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to load extension: %s", extension)

        self._register_persistent_views()
        self._register_dynamic_items()
        setup_error_handler(self)

        # Sync command dijalanin di BACKGROUND (bukan di-await langsung di
        # sini) -- setup_hook() WAJIB kelar dulu sebelum bot connect ke
        # gateway (baru abis itu bot keliatan online di Discord). Kalau
        # sync-nya kena rate limit (429), discord.py otomatis nunggu +
        # retry sampe berhasil -- kalau itu di-await langsung di sini, bot
        # bakal keliatan OFFLINE di Discord sepanjang proses nunggu itu
        # (bisa bermenit-menit, apalagi abis redeploy beberapa kali
        # beruntun). Dipisah ke task sendiri biar bot tetep online normal
        # duluan, command-nya nyusul ke-update begitu sync-nya kelar.
        self.loop.create_task(self._sync_commands())

    async def _sync_commands(self) -> None:
        try:
            await self._clear_stale_guild_commands()

            if config.guild_id:
                guild = discord.Object(id=config.guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                logger.info("Synced %d commands to guild %s.", len(synced), config.guild_id)
            else:
                synced = await self.tree.sync()
                logger.info("Synced %d global commands.", len(synced))

            # WAJIB abis blok guild sync di atas, BUKAN sebelumnya --
            # clear_commands(guild=None) ngosongin tree LOKAL yang sama
            # yang barusan dipake copy_global_to(). Kalau dipanggil
            # duluan, tree-nya kadung kosong pas giliran guild sync jalan,
            # jadi command yang ke-push ke guild ikut kosong juga (ini
            # yang bikin "ilang semua" kemarin).
            await self._clear_stale_global_commands()
        except Exception:  # noqa: BLE001
            logger.exception("Gagal sync command tree ke Discord.")

    async def _clear_stale_guild_commands(self) -> None:
        """One-time fix for duplicate slash commands: if the bot was ever
        run with GUILD_ID set, Discord keeps a guild-specific copy of every
        command in that server *in addition to* the global ones synced
        later, so the server shows both (often with outdated descriptions
        from whatever the code looked like at the time). Listing that
        server's ID in CLEAR_GUILD_COMMANDS_FOR wipes the guild-specific
        copies so only the current global commands remain visible there."""
        for guild_id in config.clear_guild_commands_for:
            guild = discord.Object(id=guild_id)
            self.tree.clear_commands(guild=guild)
            await self.tree.sync(guild=guild)
            logger.info("Cleared stale guild-specific commands for guild %s.", guild_id)

    async def _clear_stale_global_commands(self) -> None:
        """Pasangan _clear_stale_guild_commands() di atas, tapi buat arah
        sebaliknya: command GLOBAL yang nyangkut (misal dari histori
        sebelum GUILD_ID pernah di-set) -- scope-nya beda, jadi GAK
        kesentuh sama clear_commands(guild=guild) yang di atas. Cuma jalan
        kalau CLEAR_GLOBAL_COMMANDS=1 diset -- aman dibiarin nyala terus
        (no-op abis command global-nya beneran bersih), tapi boleh
        dicabut lagi abis kepake."""
        if not config.clear_global_commands:
            return
        self.tree.clear_commands(guild=None)
        await self.tree.sync()
        logger.info("Cleared stale global commands.")

    def _register_persistent_views(self) -> None:
        # Imported lazily to avoid import-order issues with bot.db being used
        # inside view callbacks before the cog package is fully loaded.
        from bot.ui.views import (
            OpenTicketPanelView,
            ShopPanelView,
            TicketClaimedView,
            TicketControlView,
            TicketReopenView,
            TicketTypeSelectView,
            CardPanelView,
            BadgePanelView,
            InvitePanelView,
        )

        self.add_view(ShopPanelView())
        self.add_view(TicketControlView())
        self.add_view(TicketClaimedView())
        self.add_view(TicketReopenView())
        self.add_view(OpenTicketPanelView())
        self.add_view(TicketTypeSelectView())
        self.add_view(CardPanelView())
        self.add_view(BadgePanelView())
        self.add_view(InvitePanelView())
        logger.info("Persistent views registered.")

    def _register_dynamic_items(self) -> None:
        # Dynamic items (order_id/rating/giveaway_id encoded directly in the
        # custom_id) are registered by class, not instance -- discord.py
        # reconstructs the right button on demand whenever a matching
        # custom_id comes in, so this survives restarts with no per-order
        # bookkeeping needed.
        from bot.ui.views import (
            OrderActionButton,
            ReplyButton,
            ReviewStartButton,
            CardRequestActionButton,
            GiveawayJoinButton,
            RobloxSlideButton,
        )
        from bot.ui.panel_reply_button import PanelReplyButton

        self.add_dynamic_items(
            OrderActionButton, ReviewStartButton, ReplyButton, PanelReplyButton,
            CardRequestActionButton, GiveawayJoinButton, RobloxSlideButton,
        )
        logger.info("Dynamic items registered.")

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id if self.user else "?")
        logger.info("NOCTRA is online across %d guild(s).", len(self.guilds))

    async def close(self) -> None:
        await self.db.close()
        await super().close()
