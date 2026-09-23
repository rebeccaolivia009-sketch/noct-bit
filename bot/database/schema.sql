-- NOCTRA database schema. SQLite now; columns/types chosen to migrate
-- cleanly to Postgres/MySQL later (explicit TEXT timestamps, no SQLite-only
-- tricks beyond AUTOINCREMENT).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT,
    emoji       TEXT,
    position    INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sits between Category and Product (Category -> Category Type -> Product).
-- Replaces the old per-product "variant" concept: instead of one product
-- having several priced sub-options, products are grouped under a type and
-- each product is its own fully independent, fully priced item.
CREATE TABLE IF NOT EXISTS category_types (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    description TEXT,
    emoji       TEXT,
    position    INTEGER NOT NULL DEFAULT 0,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS products (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    category_type_id  INTEGER NOT NULL REFERENCES category_types(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    description       TEXT,
    image_url         TEXT,
    emoji             TEXT,
    product_type      TEXT NOT NULL DEFAULT 'manual',     -- manual | automatic | digital | service
    stock_type        TEXT NOT NULL DEFAULT 'unlimited',  -- unlimited | manual
    stock_quantity    INTEGER NOT NULL DEFAULT 0,
    visible           INTEGER NOT NULL DEFAULT 1,
    base_price        REAL NOT NULL DEFAULT 0,
    currency_label    TEXT NOT NULL DEFAULT 'USD',
    discount_type     TEXT,                                -- NULL | percent | flat
    discount_value    REAL NOT NULL DEFAULT 0,
    position          INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Dynamic checkout fields now live on the category TYPE, not the product --
-- every product under a type automatically shares the same set of fields,
-- so you configure them once per type instead of once per product.
CREATE TABLE IF NOT EXISTS product_fields (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    category_type_id  INTEGER NOT NULL REFERENCES category_types(id) ON DELETE CASCADE,
    label             TEXT NOT NULL,
    field_type        TEXT NOT NULL DEFAULT 'custom',  -- username|userid|login|email|password|serverid|gameid|custom
    required          INTEGER NOT NULL DEFAULT 1,
    placeholder       TEXT,
    min_length        INTEGER NOT NULL DEFAULT 0,
    max_length        INTEGER NOT NULL DEFAULT 100,
    validation        TEXT NOT NULL DEFAULT 'none',    -- none|numeric|alpha|alphanumeric|email
    position          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS payment_methods (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    instructions     TEXT,
    image_url        TEXT,
    enabled          INTEGER NOT NULL DEFAULT 1,
    timeout_minutes  INTEGER NOT NULL DEFAULT 30,
    position         INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS orders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    product_id          INTEGER NOT NULL REFERENCES products(id),
    payment_method_id   INTEGER REFERENCES payment_methods(id),
    unit_price          REAL NOT NULL,
    total_price         REAL NOT NULL,
    currency_label      TEXT NOT NULL DEFAULT 'USD',
    status              TEXT NOT NULL DEFAULT 'pending',   -- pending|processing|completed|cancelled|refunded
    payment_status      TEXT NOT NULL DEFAULT 'pending',   -- pending|paid|expired|cancelled
    stock_reserved      INTEGER NOT NULL DEFAULT 0,
    ticket_channel_id   INTEGER,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    payment_deadline    TEXT
);

CREATE TABLE IF NOT EXISTS order_field_values (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id  INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    label     TEXT NOT NULL,
    field_type TEXT NOT NULL DEFAULT 'custom',
    value     TEXT
);

-- Messages NOCTRA sent in the customer's DM during checkout for this order
-- (order summary, payment instructions, etc.) -- tracked so they can be
-- cleaned up automatically once the order is marked completed, instead of
-- piling up in the customer's DM forever.
CREATE TABLE IF NOT EXISTS order_dm_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    channel_id  INTEGER NOT NULL,
    message_id  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tickets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id          INTEGER REFERENCES orders(id),
    user_id           INTEGER NOT NULL,
    channel_id        INTEGER NOT NULL UNIQUE,
    kind              TEXT NOT NULL DEFAULT 'order',   -- order | support
    status            TEXT NOT NULL DEFAULT 'open',    -- open | closed | archived
    close_reason      TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    closed_at         TEXT,
    last_activity_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reviews (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id             INTEGER NOT NULL UNIQUE REFERENCES orders(id),
    product_id           INTEGER NOT NULL REFERENCES products(id),
    user_id              INTEGER NOT NULL,
    rating               INTEGER NOT NULL,
    review_text          TEXT,
    image_url            TEXT,
    anonymous            INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|hidden
    awaiting_photo       INTEGER NOT NULL DEFAULT 0,
    awaiting_photo_since TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

-- Isi tombol "Reply" yang ditambahin lewat /panel atau /announcement --
-- beda sama tombol link (yang cuma nyimpen URL langsung di komponen
-- pesannya), tombol reply butuh nyimpen teks balasannya di sini biar bisa
-- dipanggil balik pas diklik, bahkan abis bot restart (custom_id-nya cuma
-- nyimpen ID row ini, bukan teksnya langsung).
CREATE TABLE IF NOT EXISTS panel_reply_buttons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL,
    reply_text  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Kartu digital NOCTRA -- saldo Credit (dari deposit customer, dipake
-- checkout), Noctoins (didapet dari transaksi bayar pake Credit, bisa jadi
-- potongan harga), dan Server Points (statistik seberapa sering belanja
-- pake card, gak bisa di-redeem). card_id itu serial/referensi doang buat
-- kebutuhan support -- BUKAN kredensial: semua aksi (cek saldo, isi saldo)
-- selalu ke-tie ke akun Discord yang invoke, bukan berdasarkan ID yang
-- diketik siapapun (lihat bot.utils.card_actions).
CREATE TABLE IF NOT EXISTS cards (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL UNIQUE,
    card_id         TEXT NOT NULL UNIQUE,
    credit_balance  REAL NOT NULL DEFAULT 0,
    noctoins        INTEGER NOT NULL DEFAULT 0,
    server_points   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Permintaan bikin kartu baru / isi saldo yang nunggu approve staff --
-- alurnya mirip bot.cogs.payment_proof (customer DM bukti transfer), tapi
-- kepisah total dari orders soalnya kartu bukan produk. Status:
-- awaiting_proof (modal disubmit, nunggu customer kirim screenshot) ->
-- pending (foto masuk, nunggu keputusan staff) -> approved | rejected.
CREATE TABLE IF NOT EXISTS card_requests (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    kind        TEXT NOT NULL,                    -- create | topup
    amount      REAL NOT NULL,
    admin_fee   REAL NOT NULL DEFAULT 0,           -- cuma keisi buat kind=create
    proof_url   TEXT,
    status      TEXT NOT NULL DEFAULT 'awaiting_proof',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);

-- Draft pesan /panel yang kesimpen (title/description/blocks/tombol/dst,
-- serialized JSON) -- biar staff bisa lanjutin edit pesan panel yang
-- udah keposting KAPAN AJA (lewat /panel message:<link/ID>), gak cuma
-- sekali sesi doang selagi PanelBuilderView masih nyangkut di memori.
-- Ke-update tiap ada perubahan draft, lihat bot.ui.panel_builder.
CREATE TABLE IF NOT EXISTS panel_drafts (
    message_id  INTEGER PRIMARY KEY,
    channel_id  INTEGER NOT NULL,
    draft_json  TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Giveaway. Tombol Join persistent (custom_id encode giveaway_id doang,
-- gaya tombol -- label/warna/emoji -- direkonstruksi dari row ini tiap
-- restart, sama triknya kayak card_requests). status: active -> ended.
-- win_role_id opsional -- kalau diisi, otomatis di-assign ke user_id
-- pemenang pas giveaway berakhir (lihat bot.utils.giveaway_actions).
CREATE TABLE IF NOT EXISTS giveaways (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id          INTEGER NOT NULL,
    channel_id        INTEGER NOT NULL,
    message_id        INTEGER,
    host_user_id      INTEGER NOT NULL,
    title             TEXT NOT NULL,
    description       TEXT,
    prize             TEXT NOT NULL,
    winner_count      INTEGER NOT NULL DEFAULT 1,
    win_role_id       INTEGER,
    button_label      TEXT NOT NULL DEFAULT 'Ikut Giveaway',
    button_style      TEXT NOT NULL DEFAULT 'primary',  -- primary|secondary|success|danger
    button_emoji      TEXT,
    color             INTEGER,
    status            TEXT NOT NULL DEFAULT 'active',   -- active|ended
    ends_at           TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    ended_at          TEXT
);

CREATE TABLE IF NOT EXISTS giveaway_entries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    giveaway_id   INTEGER NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL,
    entered_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(giveaway_id, user_id)
);

-- Dipisah dari giveaway_entries (bukan sekadar flag di situ) biar
-- /giveaway reroll bisa nimpa daftar pemenang tanpa ilangin data siapa
-- aja yang tadinya ikutan.
CREATE TABLE IF NOT EXISTS giveaway_winners (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    giveaway_id   INTEGER NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Bukti pengiriman barang yang diposting staff lewat /shipment kirim --
-- disimpen buat riwayat/audit (/shipment log), BUKAN alur approval kayak
-- reviews/card_requests -- begitu diposting langsung final, gak ada
-- status pending/approved di sini.
CREATE TABLE IF NOT EXISTS shipment_proofs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id          INTEGER NOT NULL,
    channel_id        INTEGER NOT NULL,
    message_id        INTEGER,
    staff_user_id     INTEGER NOT NULL,
    customer_user_id  INTEGER NOT NULL,
    category          TEXT NOT NULL DEFAULT '',
    product           TEXT NOT NULL DEFAULT '',
    shipped_date      TEXT NOT NULL,
    status            TEXT NOT NULL,
    photo_url         TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Jenis-jenis ticket per server (Customer Service, Konsultasi, dst) --
-- staff atur lewat /ticket type add. `slug` dipake sebagai `kind` di
-- tabel tickets & prefix nama channel, jadi divalidasi lowercase/
-- slug-safe di bot.cogs.ticket sebelum disimpen ke sini.
-- category_id/archive_category_id/log_channel_id BOLEH kosong -- kalau
-- kosong, bot.utils.ticket_actions fallback ke setting global (/settings)
-- kayak sebelum fitur ini ada, jadi "support" bawaan tetep aman.
CREATE TABLE IF NOT EXISTS ticket_types (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id              INTEGER NOT NULL,
    slug                  TEXT NOT NULL,
    label                 TEXT NOT NULL,
    description           TEXT,
    emoji                 TEXT,
    category_id           INTEGER,
    archive_category_id   INTEGER,
    log_channel_id        INTEGER,
    position              INTEGER NOT NULL DEFAULT 0,
    enabled               INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(guild_id, slug)
);

-- Badge custom leaderboard Top Spenders -- teks bebas + gradient 2 warna
-- yang user atur sendiri lewat panel /badge (CUMA berlaku buat top 1-3,
-- dicek ulang tiap refresh -- lihat bot.cogs.badge & bot.ui.views.
-- BadgePanelView), digambar langsung ke PNG leaderboard
-- (bot.utils.leaderboard_image), bukan komponen Discord.
-- UNIQUE(user_id) -- satu user cuma bisa punya satu badge aktif, set_badge
-- (bot.database.queries.leaderboard) pake ON CONFLICT(user_id) DO UPDATE.
CREATE TABLE IF NOT EXISTS leaderboard_badges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL UNIQUE,
    text        TEXT NOT NULL,
    color_from  TEXT NOT NULL,
    color_to    TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
