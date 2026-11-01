import json

from sqlalchemy import func, select

from app.db.models import Bot, Dataset, DatasetRow, FlowVersion
from app.seed.loader import seed_all

from .factories import valid_graph


def _write(tmp_path, graph=None, status="published"):
    bots, dss = tmp_path / "bots", tmp_path / "datasets"
    bots.mkdir()
    dss.mkdir()
    (dss / "dealers.json").write_text(json.dumps(
        {"name": "dealers", "description": "d", "columns": ["city"], "rows": [{"city": "Mumbai", "name": "A"}]}))
    (bots / "b.json").write_text(json.dumps(
        {"slug": "seed-bot", "name": "Seed Bot", "description": "", "industry": "auto", "avatar_color": "#000",
         "language": "en-IN", "status": status, "config": {}, "graph": graph or valid_graph()}))
    return bots, dss


async def test_seed_idempotent(db, tmp_path):
    bots, dss = _write(tmp_path)
    s = await seed_all(db, bots, dss)
    assert s["errors"] == [] and s["bots"][0]["status"] == "published"
    s = await seed_all(db, bots, dss)
    assert s["bots"][0]["new_version"] is False
    assert (await db.execute(select(func.count(Bot.id)))).scalar() == 1
    assert (await db.execute(select(func.count(FlowVersion.id)))).scalar() == 1
    assert (await db.execute(select(func.count(DatasetRow.id)))).scalar() == 1
    ds = (await db.execute(select(Dataset))).scalar_one()
    assert ds.columns == ["city", "name"]
    bot = (await db.execute(select(Bot))).scalar_one()
    assert bot.published_version_id is not None


async def test_seed_missing_dirs(db, tmp_path):
    s = await seed_all(db, tmp_path / "nope", tmp_path / "nope2")
    assert s == {"datasets": [], "bots": [], "errors": []}


async def test_seed_bad_file_reported(db, tmp_path):
    bots, dss = _write(tmp_path)
    (bots / "broken.json").write_text("{not json")
    s = await seed_all(db, bots, dss)
    assert "broken.json" in s["errors"] and len(s["bots"]) == 1


async def test_seed_real_files(db):
    s = await seed_all(db)
    assert s["errors"] == []
