"""Fixtures for replay tests: seeded Tata bot + demo oracles, fake LLM, sandboxed artifacts."""

from __future__ import annotations

import pytest

from app.main import app
from app.replay.llm import get_llm_factory
from tests.replay.fake_llm import fake_factory


@pytest.fixture(autouse=True)
def _artifacts_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    yield


@pytest.fixture
def fake_llm():
    app.dependency_overrides[get_llm_factory] = lambda: fake_factory
    yield
    app.dependency_overrides.pop(get_llm_factory, None)


@pytest.fixture
async def seeded(db_reset):
    from app.db.session import SessionLocal
    from app.seed.loader import seed_all

    async with SessionLocal() as s:
        summary = await seed_all(s)
    assert "tata-motors/tata-happy" in summary["demo_oracles"], summary
    return summary


@pytest.fixture
async def tata(client, seeded, fake_llm):
    bot = (await client.get("/api/bots/tata-motors")).json()
    oracles = {o["slug"]: o for o in (await client.get(f"/api/bots/{bot['id']}/oracles")).json()}
    return {"client": client, "bot": bot, "oracles": oracles}
