"""
Functional test of the Category -> Category Type -> Product structure and
the core catalogue/order pipeline, without needing a live Discord
connection. Run from the project root with: python tests/functional_test.py

Covers: emoji validation, category/category-type/product CRUD, dynamic
checkout fields being automatically shared by every product under a
category type, the full browsing query chain, embed rendering, and order
creation end to end.
"""

import asyncio, os, sys
sys.path.insert(0, '.')

async def main():
    path = 'data/functional_test.db'
    for ext in ('', '-wal', '-shm'):
        if os.path.exists(path + ext):
            os.remove(path + ext)

    from bot.database.core import Database
    from bot.database.queries import categories as categories_q
    from bot.database.queries import category_types as category_types_q
    from bot.database.queries import products as products_q
    from bot.database.queries import fields as fields_q
    from bot.database.queries import payments as payments_q
    from bot.database.queries import orders as orders_q
    from bot.database.queries import reviews as reviews_q
    from bot.ui import embeds
    from bot.utils.validators import is_valid_emoji, validate_field_value

    db = Database(path)
    await db.connect()
    await db.init_schema()

    print("1. is_valid_emoji checks:", is_valid_emoji("🎮"), is_valid_emoji("hello"))
    assert is_valid_emoji("🎮") is True
    assert is_valid_emoji("hello") is False

    cat_id = await categories_q.create_category(db, "ROBLOX", "Top up roblox", "🎮")
    print("2. category created:", cat_id)

    ct_id = await category_types_q.create_category_type(db, cat_id, "Adopt Me", "Adopt Me items", "🐾")
    print("3. category_type created:", ct_id)

    field_id = await fields_q.create_field(db, ct_id, "Roblox Username", "username", True, "e.g. builderman", 3, 20, "alphanumeric")
    print("4. field created on category_type:", field_id)

    product_id = await products_q.create_product(
        db, ct_id, "1000 Coins", "1000 in-game coins", "manual", "unlimited", 0, 50000.0, "IDR", None, "🪙"
    )
    print("5. product created under category_type:", product_id)

    product2_id = await products_q.create_product(
        db, ct_id, "5000 Coins", "5000 in-game coins", "manual", "unlimited", 0, 200000.0, "IDR", None, "🪙"
    )
    print("6. second product created under same category_type:", product2_id)

    # Browsing chain: category -> category_types -> products -> product detail
    categories = await categories_q.list_categories(db, enabled_only=True)
    assert len(categories) == 1 and categories[0]["emoji"] == "🎮"
    print("7. category browse OK, emoji preserved")

    types_in_cat = await category_types_q.list_category_types(db, category_id=cat_id, enabled_only=True)
    assert len(types_in_cat) == 1 and types_in_cat[0]["name"] == "Adopt Me"
    print("8. category_type browse OK")

    products_in_type = await products_q.list_products(db, category_type_id=ct_id, visible_only=True)
    assert len(products_in_type) == 2
    print("9. product list under category_type OK (2 products)")

    fields_for_type = await fields_q.list_fields(db, ct_id)
    assert len(fields_for_type) == 1 and fields_for_type[0]["label"] == "Roblox Username"
    print("10. fields inherited at category_type level OK -- both products share this field")

    # Embeds render without error
    cat_list_embed = embeds.category_list_embed(categories)
    ct_list_embed = embeds.category_type_list_embed(types_in_cat)
    prod_list_embed = embeds.product_list_embed(types_in_cat[0], products_in_type)
    rating_summary = await reviews_q.get_rating_summary(db, product_id)
    detail_embed = embeds.product_detail_embed(products_in_type[0], fields_for_type, rating_summary)
    print("11. all catalogue embeds rendered without error")
    print("    product_list title:", prod_list_embed.title)
    print("    product_detail title:", detail_embed.title)

    # Field validation using a value a customer would submit
    cleaned = validate_field_value(
        "builder123", required=True, min_length=3, max_length=20, validation="alphanumeric", label="Roblox Username"
    )
    assert cleaned == "builder123"
    print("12. field validation OK")

    # Order creation (no variant_id anymore) + order summary embed
    payment_id = await payments_q.create_payment_method(db, "QRIS", "Scan to pay", 30, "https://example.com/qris.png")
    order_id = await orders_q.create_order(db, 999999, product_id, payment_id, 50000.0, "IDR", False, 30)
    await orders_q.add_field_value(db, order_id, "Roblox Username", "username", "builder123")
    order_row = await orders_q.get_order(db, order_id)
    payment_row = await payments_q.get_payment_method(db, payment_id)
    field_values = await orders_q.get_field_values(db, order_id)
    order_embed = embeds.order_summary_embed(order_row, products_in_type[0], payment_row, field_values)
    print("13. order created + order_summary_embed rendered OK:", order_embed.title)

    await db.close()
    for ext in ('', '-wal', '-shm'):
        if os.path.exists(path + ext):
            os.remove(path + ext)
    print("\nALL FUNCTIONAL CHECKS PASSED")

asyncio.run(main())
