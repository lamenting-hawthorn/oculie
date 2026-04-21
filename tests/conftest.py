"""Shared fixtures for Polymarket Weather Prediction Agent bot tests."""

import os
import tempfile

import pytest
import aiosqlite

from bot.database import init_db


@pytest.fixture
async def db():
    """Provide a fresh temporary database for each test, cleaned up afterwards."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_openclaw_")
    os.close(fd)
    try:
        conn = await init_db(path)
        yield conn
        await conn.close()
    finally:
        if os.path.exists(path):
            os.unlink(path)
