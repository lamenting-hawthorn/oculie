#!/usr/bin/env python3
"""Resolve open paper positions in the live paper-trading database."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import bot.database as db_mod
from bot.database import init_db, set_setting
from bot.resolver import resolve_positions

DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "live_paper.db"


async def _main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the resolver against a paper-trading SQLite database."
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        help=f"SQLite database path (default: {DEFAULT_DB_PATH})",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    db = await init_db(args.db)
    db_mod._db_connection = db
    await set_setting(db, "paper_mode", "true")

    try:
        resolved = await resolve_positions(db)
        print(f"Resolved {len(resolved)} paper position(s) in {args.db}")
        return 0
    finally:
        await db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
