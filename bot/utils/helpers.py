"""Helper serbaguna yang dipakai bareng di semua cog: kalkulasi harga,
format mata uang, dan resolver runtime-settings yang nge-gabungin setting
dari DB di atas default dari `.env`.
"""

from __future__ import annotations

from bot.core.config import config
from bot.database.core import Database
from bot.database.queries import settings as settings_q


def guild_scoped_key(base: str, guild_id: int) -> str:
    """Namespace-in key setting per-guild -- dipake KHUSUS /welcome & /joinrole
    (lihat bot.cogs.welcome) biar tiap server yang bot ini nemplok punya
    pesan sambutan & auto join-role sendiri-sendiri, TANPA ubah skema tabel
    `settings` yang masih global apa adanya buat semua fitur toko lainnya
    (category/product/order/ticket/settings/dst -- itu semua SENGAJA tetep
    satu-toko-satu-config, gak per-guild, soalnya NOCTRA emang didesain
    satu toko yang kebetulan bot-nya numpang di beberapa server lain).

    Formatnya "{base}:{guild_id}". PENTING: ini beda dari key lama yang
    dipake sebelum per-guild scoping ini ada (misal "welcome_channel_id"
    polos tanpa suffix) -- key lama otomatis kebaca "belum diatur" di
    server manapun, TERMASUK server utama yang udah pernah di-setup
    sebelumnya. Staff perlu `/welcome setup` + `/welcome channel` ulang
    sekali buat server utama abis migrasi ini (data lama gak kehapus,
    cuma gak kebaca lagi)."""
    return f"{base}:{guild_id}"


def is_video_url(url: str) -> bool:
    """Deteksi kasar apakah URL attachment itu video, berdasarkan ekstensi
    file-nya doang (bukan content_type, soalnya beberapa caller cuma punya
    URL mentah di tangan, gak ada objek discord.Attachment aslinya lagi).
    Dipake bot.utils.order_actions.forward_to_staff() biar video gak
    dipaksain masuk slot embed.set_image() (yang cuma nerima gambar,
    videonya bakal gagal render)."""
    path = url.split("?")[0].lower()
    return path.endswith((".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"))


def calculate_final_price(
    base_price: float, discount_type: str | None, discount_value: float
) -> float:
    """Terapin diskon ke harga dasar. Selalu return float yang gak minus."""
    if not discount_type or discount_value <= 0:
        return round(max(0.0, base_price), 2)
    if discount_type == "percent":
        final = base_price - (base_price * (discount_value / 100))
    elif discount_type == "flat":
        final = base_price - discount_value
    else:
        final = base_price
    return round(max(0.0, final), 2)


def format_price(amount: float, currency_label: str) -> str:
    return f"{amount:,.2f} {currency_label}"


def discount_label(discount_type: str | None, discount_value: float) -> str | None:
    if not discount_type or discount_value <= 0:
        return None
    if discount_type == "percent":
        return f"-{discount_value:g}%"
    if discount_type == "flat":
        return f"-{discount_value:g}"
    return None


class RuntimeSettings:
    """Resolve setting yang efektif: override DB -> default .env."""

    def __init__(self, db: Database) -> None:
        self.db = db

    async def _get(self, key: str, env_default):
        value = await settings_q.get_setting(self.db, key)
        if value is None:
            return env_default
        return value

    async def _get_id_list(self, key: str) -> list[int]:
        """Parse setting yang nyimpen list ID sebagai string dipisah koma
        (dipake buat leaderboard_excluded_users, join_role_user_ids,
        join_role_bot_ids, dst)."""
        value = await self._get(key, "")
        if not value:
            return []
        ids: list[int] = []
        for piece in str(value).split(","):
            piece = piece.strip()
            if piece.isdigit():
                ids.append(int(piece))
        return ids

    async def staff_role_id(self) -> int | None:
        value = await self._get("staff_role_id", config.staff_role_id)
        return int(value) if value else None

    async def order_log_channel_id(self) -> int | None:
        value = await self._get("order_log_channel_id", None)
        return int(value) if value else None

    async def reviews_channel_id(self) -> int | None:
        """Channel publik tempat review yang udah di-approve otomatis
        diposting buat semua orang liat -- reputasi toko / social proof,
        bukan antrian moderasi staff."""
        value = await self._get("reviews_channel_id", None)
        return int(value) if value else None

    async def leaderboard_channel_id(self) -> int | None:
        value = await self._get("leaderboard_channel_id", None)
        return int(value) if value else None

    async def leaderboard_excluded_user_ids(self) -> list[int]:
        """User ID yang manual disembunyiin dari leaderboard Top Spenders
        lewat /settings leaderboard_exclude -- misal akun staff/tester yang
        dipake buat nyoba checkout, yang spend-nya gak seharusnya kehitung
        di leaderboard publik. Disimpan sebagai string ID dipisah koma."""
        return await self._get_id_list("leaderboard_excluded_users")

    async def leaderboard_badge_role_id(self) -> int | None:
        """Role yang WAJIB dipunya user buat bisa atur badge custom-nya
        sendiri lewat panel /badge channel -- diatur staff lewat
        /badge role. Kosong = fitur badge custom dianggap belum
        diaktifin, gak ada yang bisa atur apa-apa."""
        value = await self._get("leaderboard_badge_role_id", None)
        return int(value) if value else None

    async def leaderboard_badge_channel_id(self) -> int | None:
        value = await self._get("leaderboard_badge_channel_id", None)
        return int(value) if value else None

    async def leaderboard_background_url(self) -> str | None:
        """URL gambar background leaderboard (biasanya logo/icon store) --
        diatur staff lewat /badge background. Kosong = pake gradient
        polos bawaan kayak sebelum fitur ini ada."""
        value = await self._get("leaderboard_background_url", None)
        return value or None

    async def purchase_feed_channel_id(self) -> int | None:
        """Channel publik tempat kartu "Si X baru aja beli Y" diposting
        tiap ada order yang ditandain selesai -- diatur lewat
        /settings purchase_feed_channel."""
        value = await self._get("purchase_feed_channel_id", None)
        return int(value) if value else None

    async def ad_channel_id(self) -> int | None:
        """Channel default buat /iklan kalau parameter channel-nya gak
        diisi -- diatur lewat /settings ad_channel. Staff tetep bisa
        override channel tujuan tiap kali posting iklan lewat parameter
        `channel` di command itu sendiri."""
        value = await self._get("ad_channel_id", None)
        return int(value) if value else None

    async def main_server_invite_url(self) -> str | None:
        """Link invite server utama, ditampilin sebagai tombol "Join
        Server" abis customer selesai kasih review -- diatur lewat
        /settings main_server_invite."""
        return await self._get("main_server_invite_url", None)

    async def review_banner_url(self) -> str | None:
        """Gambar banner default buat kartu review publik, dipake kalau
        customer gak nyertain foto review sendiri -- diatur lewat
        /settings review_banner_image. Kalau belum diatur, kartu review
        tanpa foto ya tampil tanpa banner sama sekali (gak fallback ke
        apapun)."""
        return await self._get("review_banner_url", None)

    async def ticket_category_id(self) -> int | None:
        value = await self._get("ticket_category_id", config.ticket_category_id)
        return int(value) if value else None

    async def ticket_archive_category_id(self) -> int | None:
        value = await self._get(
            "ticket_archive_category_id", config.ticket_archive_category_id
        )
        return int(value) if value else None

    async def ticket_log_channel_id(self) -> int | None:
        value = await self._get("ticket_log_channel_id", config.ticket_log_channel_id)
        return int(value) if value else None

    async def ticket_auto_archive_hours(self) -> int:
        value = await self._get(
            "ticket_auto_archive_hours", config.ticket_auto_archive_hours
        )
        return int(value)

    async def default_currency(self) -> str:
        value = await self._get("default_currency", config.default_currency)
        return str(value)

    # -- Pesan sambutan member baru (/welcome) ---------------------------------
    # Method di bawah ini SEMUANYA per-guild (parameter guild_id wajib) --
    # beda dari method lain di kelas ini yang masih global. Lihat docstring
    # guild_scoped_key() di atas buat alasannya.

    async def welcome_enabled(self, guild_id: int) -> bool:
        value = await self._get(guild_scoped_key("welcome_enabled", guild_id), "1")
        return str(value) == "1"

    async def welcome_mention_enabled(self, guild_id: int) -> bool:
        """Apakah member yang baru gabung di-ping (lewat message content)
        pas pesan sambutan diposting -- default nyala, diatur lewat
        /welcome mention."""
        value = await self._get(guild_scoped_key("welcome_mention_enabled", guild_id), "1")
        return str(value) == "1"

    async def welcome_channel_id(self, guild_id: int) -> int | None:
        value = await self._get(guild_scoped_key("welcome_channel_id", guild_id), None)
        return int(value) if value else None

    async def welcome_title(self, guild_id: int) -> str | None:
        """Template judul embed sambutan -- None berarti belum diatur,
        caller fallback ke default bawaan. String kosong (hasil ngosongin
        field pas /welcome setup) DIANGGEP sama kayak belum diatur, biar
        staff bisa "reset ke default" cukup dengan ngosongin field-nya."""
        value = await self._get(guild_scoped_key("welcome_title", guild_id), None)
        return value or None

    async def welcome_description(self, guild_id: int) -> str | None:
        value = await self._get(guild_scoped_key("welcome_description", guild_id), None)
        return value or None

    async def welcome_banner_url(self, guild_id: int) -> str | None:
        """URL gambar banner full-width buat pesan sambutan -- HARUS URL
        yang udah di-hosting (bukan upload attachment), soalnya pesan ini
        diposting otomatis berkali-kali setiap ada yang gabung, gak kayak
        /iklan yang cuma sekali kirim manual."""
        value = await self._get(guild_scoped_key("welcome_banner_url", guild_id), None)
        return value or None

    async def welcome_footer_text(self, guild_id: int) -> str | None:
        value = await self._get(guild_scoped_key("welcome_footer_text", guild_id), None)
        return value or None

    async def welcome_color(self, guild_id: int) -> int | None:
        value = await self._get(guild_scoped_key("welcome_color", guild_id), None)
        if not value:
            return None
        try:
            return int(str(value))
        except ValueError:
            return None

    # -- Notifikasi Server Boost (/boost) --------------------------------------
    # Per-guild, sama pola kayak /welcome di atas -- lihat guild_scoped_key().

    async def boost_enabled(self, guild_id: int) -> bool:
        value = await self._get(guild_scoped_key("boost_enabled", guild_id), "0")
        return str(value) == "1"

    async def boost_channel_id(self, guild_id: int) -> int | None:
        value = await self._get(guild_scoped_key("boost_channel_id", guild_id), None)
        return int(value) if value else None

    async def boost_mention_enabled(self, guild_id: int) -> bool:
        value = await self._get(guild_scoped_key("boost_mention_enabled", guild_id), "0")
        return str(value) == "1"

    async def boost_title(self, guild_id: int) -> str | None:
        value = await self._get(guild_scoped_key("boost_title", guild_id), None)
        return value or None

    async def boost_description(self, guild_id: int) -> str | None:
        value = await self._get(guild_scoped_key("boost_description", guild_id), None)
        return value or None

    async def boost_banner_url(self, guild_id: int) -> str | None:
        value = await self._get(guild_scoped_key("boost_banner_url", guild_id), None)
        return value or None

    async def boost_footer_text(self, guild_id: int) -> str | None:
        value = await self._get(guild_scoped_key("boost_footer_text", guild_id), None)
        return value or None

    async def boost_color(self, guild_id: int) -> int | None:
        value = await self._get(guild_scoped_key("boost_color", guild_id), None)
        if not value:
            return None
        try:
            return int(str(value))
        except ValueError:
            return None

    # -- Bukti Pengiriman Barang (/shipment) -----------------------------------
    # Per-guild, sama pola kayak /welcome & /boost -- lihat guild_scoped_key().

    async def shipment_channel_id(self, guild_id: int) -> int | None:
        value = await self._get(guild_scoped_key("shipment_channel_id", guild_id), None)
        return int(value) if value else None

    async def shipment_emoji_title(self, guild_id: int) -> str | None:
        value = await self._get(guild_scoped_key("shipment_emoji_title", guild_id), None)
        return value or None

    async def shipment_emoji_date(self, guild_id: int) -> str | None:
        value = await self._get(guild_scoped_key("shipment_emoji_date", guild_id), None)
        return value or None

    async def shipment_emoji_customer(self, guild_id: int) -> str | None:
        value = await self._get(guild_scoped_key("shipment_emoji_customer", guild_id), None)
        return value or None

    async def shipment_emoji_category(self, guild_id: int) -> str | None:
        value = await self._get(guild_scoped_key("shipment_emoji_category", guild_id), None)
        return value or None

    async def shipment_emoji_product(self, guild_id: int) -> str | None:
        value = await self._get(guild_scoped_key("shipment_emoji_product", guild_id), None)
        return value or None

    async def shipment_emoji_status(self, guild_id: int) -> str | None:
        value = await self._get(guild_scoped_key("shipment_emoji_status", guild_id), None)
        return value or None

    # -- Auto join-role (/joinrole) --------------------------------------------
    # Sama kayak /welcome di atas -- per-guild, lihat guild_scoped_key().

    async def join_role_user_ids(self, guild_id: int) -> list[int]:
        """Role yang otomatis kepasang ke MEMBER BIASA (bukan bot) pas
        gabung -- diatur lewat /joinrole add target:user."""
        return await self._get_id_list(guild_scoped_key("join_role_user_ids", guild_id))

    async def join_role_bot_ids(self, guild_id: int) -> list[int]:
        """Role yang otomatis kepasang ke BOT pas ditambahin ke server --
        diatur lewat /joinrole add target:bot."""
        return await self._get_id_list(guild_scoped_key("join_role_bot_ids", guild_id))

    # -- Status toko (/storestatus) -------------------------------------------

    async def store_status_channel_id(self) -> int | None:
        """Channel tempat embed status toko diposting/di-update --
        diatur lewat /storestatus channel."""
        value = await self._get("store_status_channel_id", None)
        return int(value) if value else None

    async def store_status_message_id(self) -> int | None:
        """ID pesan embed status toko yang lagi aktif, dipake buat edit-in-place
        pas staff toggle buka/tutup (bukan kirim pesan baru tiap kali) --
        di-reset ke kosong kalau channel-nya diganti."""
        value = await self._get("store_status_message_id", None)
        return int(value) if value else None

    async def store_status_state(self) -> str:
        """State toko sekarang: 'open' atau 'closed'. Default 'closed'
        sampe staff toggle manual lewat /storestatus open|close -- gak ada
        jadwal otomatis, semuanya manual."""
        value = await self._get("store_status_state", "closed")
        return str(value) if value in ("open", "closed") else "closed"

    async def store_status_note(self) -> str | None:
        """Catetan opsional yang nempel di bawah status (misal 'balik lagi
        jam 9 pagi WIB') -- diisi tiap kali /storestatus open|close dipanggil,
        kosong kalau staff gak ngisi parameter catatan."""
        return await self._get("store_status_note", None)

    async def store_status_emoji_open(self) -> str:
        """Emoji custom buat indikator status BUKA -- diatur lewat
        /storestatus emoji, default emoji bulet hijau bawaan Discord."""
        value = await self._get("store_status_emoji_open", "\U0001F7E2")
        return str(value)

    async def store_status_emoji_closed(self) -> str:
        """Emoji custom buat indikator status TUTUP -- diatur lewat
        /storestatus emoji, default emoji bulet merah bawaan Discord."""
        value = await self._get("store_status_emoji_closed", "\U0001F534")
        return str(value)

    async def store_status_thumbnail_url(self) -> str | None:
        """URL gambar thumbnail kecil di pojok kanan atas embed status toko --
        diatur lewat /storestatus thumbnail. HARUS URL yang udah di-hosting
        (bukan upload attachment), soalnya pesan ini diedit berkali-kali tiap
        staff toggle, sama alasannya kayak welcome_banner_url."""
        value = await self._get("store_status_thumbnail_url", None)
        return value or None

    # -- Kartu digital (/card, /settings card_*) --------------------------------

    async def card_requests_channel_id(self) -> int | None:
        """Channel tempat permintaan bikin kartu/isi saldo diteruskan buat
        staff approve/reject -- diatur lewat /settings card_requests_channel."""
        value = await self._get("card_requests_channel_id", None)
        return int(value) if value else None

    async def card_admin_fee(self) -> float:
        """Biaya admin pembuatan kartu -- nominal TETAP (bukan persenan),
        dipotong sekali doang pas kartu dibuat, gak kepake lagi pas isi
        saldo berikutnya. Default 5000, diatur lewat /settings card_admin_fee."""
        value = await self._get("card_admin_fee", "5000")
        try:
            return float(value)
        except (TypeError, ValueError):
            return 5000.0

    async def card_noctoin_rate(self) -> float:
        """Satu rate buat DUA arah: berapa rupiah belanja (pake Kartu
        NOCTRA) buat dapet 1 Noctoin, SEKALIGUS berapa rupiah potongan
        harga per 1 Noctoin yang dipake. Default 10000 (belanja 10rb dapet
        1 Noctoin, 1 Noctoin motong harga 10rb), diatur lewat
        /settings card_noctoin_rate."""
        value = await self._get("card_noctoin_rate", "10000")
        try:
            return float(value)
        except (TypeError, ValueError):
            return 10000.0

    async def activity_log_channel_id(self) -> int | None:
        """Channel tempat log aktivitas staff diposting (order, kartu,
        moderasi review, perubahan settings) -- diatur lewat
        /settings activity_log_channel. Kosong = fitur ini mati total,
        gak ada yang keposting kemanapun."""
        value = await self._get("activity_log_channel_id", None)
        return int(value) if value else None

    # -- Kartu review publik (/settings review_emoji) --------------------------
    # Dipake components.review_card_container() -- diposting ke
    # /settings reviews_channel abis staff approve review.

    async def review_card_emoji_title(self) -> str:
        return str(await self._get("review_card_emoji_title", "\U0001F31F"))

    async def review_card_emoji_user(self) -> str:
        return str(await self._get("review_card_emoji_user", "\U0001F464"))

    async def review_card_emoji_product(self) -> str:
        return str(await self._get("review_card_emoji_product", "\U0001F4E6"))

    async def review_card_emoji_rating(self) -> str:
        """Emoji di DEPAN label 'Rating' -- beda dari
        review_card_emoji_star_filled/_star_empty di bawah, yang itu buat
        ngisi baris bintangnya sendiri."""
        return str(await self._get("review_card_emoji_rating", "\u2b50"))

    async def review_card_emoji_star_filled(self) -> str:
        return str(await self._get("review_card_emoji_star_filled", "\u2b50"))

    async def review_card_emoji_star_empty(self) -> str:
        return str(await self._get("review_card_emoji_star_empty", "\u2606"))

    async def review_card_emoji_message(self) -> str:
        return str(await self._get("review_card_emoji_message", "\U0001F4AC"))

    # -- Notifikasi bukti foto review (/settings testi_proof_channel) ----------
    # Dipake bot.cogs.review_photo -- BEDA dari reviews_channel di atas: ini
    # notif INTERNAL staff langsung begitu foto masuk, bukan showcase publik
    # yang nunggu approve.

    async def testi_proof_channel_id(self) -> int | None:
        value = await self._get("testi_proof_channel_id", None)
        return int(value) if value else None

    async def testi_proof_emoji_title(self) -> str:
        return str(await self._get("testi_proof_emoji_title", "\U0001F4B0"))

    async def testi_proof_emoji_buyer(self) -> str:
        return str(await self._get("testi_proof_emoji_buyer", "\U0001F464"))

    async def testi_proof_emoji_product(self) -> str:
        return str(await self._get("testi_proof_emoji_product", "\U0001F4E6"))

    async def testi_proof_emoji_price(self) -> str:
        return str(await self._get("testi_proof_emoji_price", "\U0001F4B5"))

    async def testi_proof_emoji_testi(self) -> str:
        return str(await self._get("testi_proof_emoji_testi", "\U0001F31F"))

    # -- Invite Tracker (/invite) ------------------------------------------
    # notif_channel & leaderboard_channel GLOBAL (bukan guild_scoped_key)
    # -- konsisten sama leaderboard_channel_id punya Top Spenders, satu
    # config buat satu instance bot. leaderboard_message_id-nya sendiri
    # yang per-guild (lihat bot.database.queries.invites), soalnya satu
    # instance bot yang numpang di banyak server tetep butuh nge-track
    # pesan leaderboard yang beda-beda per server.

    async def invite_notif_channel_id(self) -> int | None:
        """Channel tempat notif "siapa invite siapa" diposting tiap ada
        join/leave yang ke-attribute ke invite tracker -- diatur lewat
        /invite settings notif_channel."""
        value = await self._get("invite_notif_channel_id", None)
        return int(value) if value else None

    async def invite_leaderboard_channel_id(self) -> int | None:
        value = await self._get("invite_leaderboard_channel_id", None)
        return int(value) if value else None

    async def invite_rules_text(self) -> str | None:
        """Isi tombol "Aturan Main" di panel /invite -- diatur lewat
        /invite settings rules. None kalau staff belum pernah atur."""
        return await self._get("invite_rules_text", None)
