"""Test setup: uses Postgres `voiceflow_test` on :5433 when reachable, else sqlite (aiosqlite).

Override with TEST_DATABASE_URL. Env vars must be set before `app` is imported.
"""

from __future__ import annotations

import os
import socket
import tempfile


def _pg_up(host: str = "localhost", port: int = 5433) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


_url = os.environ.get("TEST_DATABASE_URL")
if not _url:
    if _pg_up():
        _url = "postgresql+asyncpg://voiceflow:voiceflow@localhost:5433/voiceflow_test"
    else:
        _url = f"sqlite+aiosqlite:///{tempfile.gettempdir()}/voiceflow_test.sqlite3"
os.environ["DATABASE_URL"] = _url
os.environ["DB_NULLPOOL"] = "true"
os.environ["AUTO_SEED"] = "false"

import httpx  # noqa: E402
import pytest  # noqa: E402

from app.db.session import SessionLocal, drop_db, init_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
async def db_reset():
    await drop_db()
    await init_db()
    yield


@pytest.fixture
async def client(db_reset):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
async def db(db_reset):
    async with SessionLocal() as s:
        yield s
