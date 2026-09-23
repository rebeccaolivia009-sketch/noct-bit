"""
Thin async SQLite wrapper around aiosqlite.

Kept deliberately simple: one shared connection, WAL mode for better
concurrent read/write behaviour, and a handful of helper methods
(`execute`, `fetchone`, `fetchall`, `executescript`) that every query module
in `bot/database/queries/` builds on. This is what makes a future migration
to Postgres/MySQL straightforward -- only this file and the queries would
need to change, not the cogs.
"""

from __future__ import annotations

import os
from pathlib import Path

import aiosqlite

from bot.core.logger import logger


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(os.path.dirname(self.path) or ".").mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA foreign_keys = ON;")
        await self.conn.execute("PRAGMA journal_mode = WAL;")
        await self.conn.commit()
        logger.info("Database connected at %s", self.path)

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()
            logger.info("Database connection closed.")

    async def init_schema(self) -> None:
        schema_path = Path(__file__).parent / "schema.sql"
        sql = schema_path.read_text(encoding="utf-8")
        assert self.conn is not None
        await self.conn.executescript(sql)
        await self.conn.commit()
        await self._migrate_category_hierarchy()
        await self._run_migrations()
        await self._create_indexes()
        logger.info("Database schema ensured.")

    async def _create_indexes(self) -> None:
        """Indexes are created here -- after table creation AND migrations
        -- rather than inside schema.sql, because some of them reference
        columns (like products.category_type_id) that only exist on an
        existing database once _migrate_category_hierarchy() has added
        them. Each is wrapped individually so one unexpected failure can't
        block the others or stop the bot from starting."""
        assert self.conn is not None
        statements = [
            "CREATE INDEX IF NOT EXISTS idx_category_types_category ON category_types(category_id)",
            "CREATE INDEX IF NOT EXISTS idx_products_category_type ON products(category_type_id)",
            "CREATE INDEX IF NOT EXISTS idx_fields_category_type ON product_fields(category_type_id)",
            "CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, payment_status)",
            "CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)",
            "CREATE INDEX IF NOT EXISTS idx_reviews_product ON reviews(product_id, status)",
            "CREATE INDEX IF NOT EXISTS idx_giveaways_status ON giveaways(status)",
            "CREATE INDEX IF NOT EXISTS idx_giveaway_entries_giveaway ON giveaway_entries(giveaway_id)",
            "CREATE INDEX IF NOT EXISTS idx_shipment_proofs_guild ON shipment_proofs(guild_id, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_ticket_types_guild ON ticket_types(guild_id)",
        ]
        for statement in statements:
            try:
                await self.conn.execute(statement)
            except Exception:  # noqa: BLE001
                logger.warning("Could not create index (%s) -- continuing anyway.", statement)
        await self.conn.commit()

    async def _migrate_category_hierarchy(self) -> None:
        """One-time structural migration: introduces `category_types`
        between categories and products (Category -> Category Type ->
        Product), replacing the old per-product variant model. Safe to run
        on every startup -- it only does anything the first time, detected
        by whether `products.category_type_id` already exists."""
        assert self.conn is not None
        conn = self.conn

        cursor = await conn.execute("PRAGMA table_info(products)")
        product_columns = {row[1] for row in await cursor.fetchall()}
        await cursor.close()

        if "category_type_id" in product_columns or "category_id" not in product_columns:
            return  # already migrated, or a fresh install that never had the old shape

        logger.info("Migrating products to the Category -> Category Type -> Product structure...")

        rows_cursor = await conn.execute("SELECT DISTINCT category_id FROM products")
        category_rows = await rows_cursor.fetchall()
        await rows_cursor.close()

        category_to_type: dict[int, int] = {}
        for (category_id,) in category_rows:
            insert_cursor = await conn.execute(
                "INSERT INTO category_types (category_id, name) VALUES (?, ?)",
                (category_id, "General"),
            )
            category_to_type[category_id] = insert_cursor.lastrowid

        await conn.execute("ALTER TABLE products ADD COLUMN category_type_id INTEGER")
        for category_id, category_type_id in category_to_type.items():
            await conn.execute(
                "UPDATE products SET category_type_id = ? WHERE category_id = ?",
                (category_type_id, category_id),
            )
        await conn.commit()
        logger.info(
            "Created %d 'General' category type(s) and reassigned existing products to them.",
            len(category_to_type),
        )

        # product_fields moves from per-product to per-category-type.
        field_cursor = await conn.execute("PRAGMA table_info(product_fields)")
        field_columns = {row[1] for row in await field_cursor.fetchall()}
        await field_cursor.close()

        if "product_id" in field_columns and "category_type_id" not in field_columns:
            await conn.execute("ALTER TABLE product_fields ADD COLUMN category_type_id INTEGER")
            rows_cursor = await conn.execute("SELECT id, product_id FROM product_fields")
            field_rows = await rows_cursor.fetchall()
            await rows_cursor.close()
            for field_id, product_id in field_rows:
                product_cursor = await conn.execute(
                    "SELECT category_type_id FROM products WHERE id = ?", (product_id,)
                )
                product_row = await product_cursor.fetchone()
                await product_cursor.close()
                if product_row:
                    await conn.execute(
                        "UPDATE product_fields SET category_type_id = ? WHERE id = ?",
                        (product_row[0], field_id),
                    )
            await conn.commit()
            logger.info(
                "Moved %d checkout field(s) onto their product's new category type. "
                "If several products shared one old category with different fields, "
                "review with /category_type field list and remove any duplicates.",
                len(field_rows),
            )

        # Old columns (products.category_id, product_fields.product_id) are
        # left in place unused rather than dropped -- SQLite refuses to drop
        # a column involved in a foreign key or index, which both of these
        # are, so attempting it would just fail anyway. They're harmless.

    async def _run_migrations(self) -> None:
        """Lightweight forward-only migrations for columns added after a
        table already existed in someone's deployed database. Each entry is
        (table, column, ddl-fragment); skipped automatically if the column
        is already there, so this is always safe to run on every startup."""
        assert self.conn is not None
        migrations = [
            ("payment_methods", "image_url", "ALTER TABLE payment_methods ADD COLUMN image_url TEXT"),
            ("categories", "emoji", "ALTER TABLE categories ADD COLUMN emoji TEXT"),
            ("products", "emoji", "ALTER TABLE products ADD COLUMN emoji TEXT"),
            ("reviews", "image_url", "ALTER TABLE reviews ADD COLUMN image_url TEXT"),
            ("reviews", "awaiting_photo", "ALTER TABLE reviews ADD COLUMN awaiting_photo INTEGER NOT NULL DEFAULT 0"),
            ("reviews", "awaiting_photo_since", "ALTER TABLE reviews ADD COLUMN awaiting_photo_since TEXT"),
            ("tickets", "claimed_by", "ALTER TABLE tickets ADD COLUMN claimed_by INTEGER"),
            ("orders", "payment_proof_url", "ALTER TABLE orders ADD COLUMN payment_proof_url TEXT"),
            ("orders", "paid_with_credit", "ALTER TABLE orders ADD COLUMN paid_with_credit INTEGER NOT NULL DEFAULT 0"),
            ("orders", "noctoins_used", "ALTER TABLE orders ADD COLUMN noctoins_used INTEGER NOT NULL DEFAULT 0"),
            ("payment_methods", "emoji", "ALTER TABLE payment_methods ADD COLUMN emoji TEXT"),
            ("shipment_proofs", "category", "ALTER TABLE shipment_proofs ADD COLUMN category TEXT NOT NULL DEFAULT ''"),
            ("shipment_proofs", "product", "ALTER TABLE shipment_proofs ADD COLUMN product TEXT NOT NULL DEFAULT ''"),
        ]
        for table, column, ddl in migrations:
            cursor = await self.conn.execute(f"PRAGMA table_info({table})")
            columns = {row[1] for row in await cursor.fetchall()}
            await cursor.close()
            if column not in columns:
                await self.conn.execute(ddl)
                await self.conn.commit()
                logger.info("Migration applied: %s.%s", table, column)

    async def execute(self, query: str, params: tuple = ()) -> int | None:
        assert self.conn is not None
        cursor = await self.conn.execute(query, params)
        await self.conn.commit()
        return cursor.lastrowid

    async def executemany(self, query: str, seq_of_params) -> None:
        assert self.conn is not None
        await self.conn.executemany(query, seq_of_params)
        await self.conn.commit()

    async def fetchone(self, query: str, params: tuple = ()) -> aiosqlite.Row | None:
        assert self.conn is not None
        cursor = await self.conn.execute(query, params)
        row = await cursor.fetchone()
        await cursor.close()
        return row

    async def fetchall(self, query: str, params: tuple = ()) -> list[aiosqlite.Row]:
        assert self.conn is not None
        cursor = await self.conn.execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return list(rows)
