"""
Local smoke test: exercises every cog's command/group registration and the
persistent view setup WITHOUT connecting to Discord's gateway or calling
tree.sync() (no network access needed). Run from the project root with:
python tests/smoke_test.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("DISCORD_TOKEN", "test-token-not-real")
os.environ.setdefault("DATABASE_PATH", "data/smoke_test.db")

from bot.core.bot import NoctraBot, EXTENSIONS  # noqa: E402


async def main():
    bot = NoctraBot()
    await bot.db.connect()
    await bot.db.init_schema()

    for ext in EXTENSIONS:
        await bot.load_extension(ext)
        print(f"OK  loaded {ext}")

    bot._register_persistent_views()
    print("OK  persistent views registered")

    bot._register_dynamic_items()
    print("OK  dynamic items registered")

    commands = bot.tree.get_commands()
    total = 0

    def count(cmd, prefix=""):
        nonlocal total
        from discord import app_commands
        if isinstance(cmd, app_commands.Group):
            for sub in cmd.commands:
                count(sub, prefix + cmd.name + " ")
        else:
            total += 1
            print(f"  /{prefix}{cmd.name}")

    print(f"\nRegistered top-level command tree entries: {len(commands)}")
    for c in commands:
        count(c)
    print(f"\nTotal leaf slash commands: {total}")

    await bot.db.close()
    print("\nSMOKE TEST PASSED")


asyncio.run(main())
