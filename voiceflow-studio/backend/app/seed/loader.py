"""Seed loader: upserts datasets and bots from JSON files.

    uv run python -m app.seed.loader            # idempotent upsert
    uv run python -m app.seed.loader --reset    # drop all tables, recreate, then seed

Bot JSON:     {slug, name, description, industry, avatar_color, language, config: BotConfig,
               graph: FlowGraph, status?: 'draft'|'published'}
Dataset JSON: {name, description, columns, rows: [{...}]}

Bots are matched by slug. A new flow version is only added when the graph differs from the
latest version. ``status: "published"`` publishes the seeded version; ``"draft"``/``"archived"``
are honoured; with no status the bot is published only if its graph validates without errors. Datasets are matched by name and their rows are replaced.
Missing/empty seed directories are fine.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Bot, Dataset, DatasetRow, FlowVersion, utcnow
from app.schemas.flow import FlowGraph
from app.schemas.providers import BotConfig
from app.services.flow_validator import validate_flow

SEED_DIR = Path(__file__).resolve().parent
BOTS_DIR = SEED_DIR / "bots"
DATASETS_DIR = SEED_DIR / "datasets"


def _json_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.json") if p.is_file())


def seed_files_exist(bots_dir: Path = BOTS_DIR, datasets_dir: Path = DATASETS_DIR) -> bool:
    return bool(_json_files(bots_dir) or _json_files(datasets_dir))


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"seed: cannot read {path.name}: {e}")
        return None
    if not isinstance(data, dict):
        logger.error(f"seed: {path.name} must contain a JSON object")
        return None
    return data


async def upsert_dataset(db: AsyncSession, data: dict[str, Any]) -> Dataset:
    name = data["name"]
    rows = [r for r in data.get("rows") or [] if isinstance(r, dict)]
    columns = list(data.get("columns") or [])
    for r in rows:
        for k in r:
            if k not in columns:
                columns.append(k)
    ds = (await db.execute(select(Dataset).where(Dataset.name == name))).scalar_one_or_none()
    if ds is None:
        ds = Dataset(name=name)
        db.add(ds)
    ds.description = data.get("description") or ""
    ds.columns = columns
    await db.flush()
    await db.execute(delete(DatasetRow).where(DatasetRow.dataset_id == ds.id))
    db.add_all([DatasetRow(dataset_id=ds.id, data=r) for r in rows])
    await db.flush()
    return ds


async def upsert_bot(db: AsyncSession, data: dict[str, Any], datasets: set[str]) -> tuple[Bot, bool]:
    """Returns (bot, new_version_created)."""
    slug = data["slug"]
    graph = FlowGraph.model_validate(data["graph"]).model_dump(mode="json")
    config = BotConfig.model_validate(data.get("config") or {}).model_dump(mode="json")

    bot = (await db.execute(select(Bot).where(Bot.slug == slug))).scalar_one_or_none()
    if bot is None:
        bot = Bot(slug=slug)
        db.add(bot)
    bot.name = data.get("name") or slug
    bot.description = data.get("description") or ""
    bot.industry = data.get("industry") or ""
    bot.avatar_color = data.get("avatar_color") or "#6366f1"
    bot.language = data.get("language") or "en-IN"
    bot.config = config
    bot.updated_at = utcnow()
    await db.flush()

    latest = (
        await db.execute(
            select(FlowVersion).where(FlowVersion.bot_id == bot.id).order_by(FlowVersion.version.desc()).limit(1)
        )
    ).scalar_one_or_none()
    created = False
    if latest is None or latest.graph != graph:
        next_v = (latest.version + 1) if latest else 1
        latest = FlowVersion(bot_id=bot.id, version=next_v, graph=graph, note="Seeded")
        db.add(latest)
        await db.flush()
        created = True

    wanted = data.get("status")
    result = validate_flow(graph, datasets=datasets)
    errs = [i.code for i in result.issues if i.severity == "error"]
    if wanted == "draft":
        bot.status = "draft"
    elif wanted == "archived":
        bot.status = "archived"
    elif wanted == "published" or result.valid:
        if errs:
            logger.warning(f"seed: bot '{slug}' published despite validation errors {errs}")
        bot.published_version_id = latest.id
        bot.status = "published"
    else:
        logger.warning(f"seed: bot '{slug}' has validation errors {errs}; left as draft")
        if bot.published_version_id is None:
            bot.status = "draft"
    await db.flush()
    return bot, created


async def seed_all(
    db: AsyncSession, bots_dir: Path = BOTS_DIR, datasets_dir: Path = DATASETS_DIR
) -> dict[str, Any]:
    summary: dict[str, Any] = {"datasets": [], "bots": [], "errors": []}
    for path in _json_files(datasets_dir):
        data = _read(path)
        if data is None or not data.get("name"):
            summary["errors"].append(path.name)
            continue
        try:
            async with db.begin_nested():
                ds = await upsert_dataset(db, data)
            summary["datasets"].append(ds.name)
        except Exception as e:  # noqa: BLE001
            logger.error(f"seed: dataset {path.name} failed: {e}")
            summary["errors"].append(path.name)

    known = set((await db.execute(select(Dataset.name))).scalars().all())
    for path in _json_files(bots_dir):
        data = _read(path)
        if data is None or not data.get("slug") or "graph" not in data:
            summary["errors"].append(path.name)
            continue
        try:
            async with db.begin_nested():
                bot, created = await upsert_bot(db, data, known)
            summary["bots"].append({"slug": bot.slug, "status": bot.status, "new_version": created})
        except (ValidationError, Exception) as e:  # noqa: BLE001
            logger.error(f"seed: bot {path.name} failed: {e}")
            summary["errors"].append(path.name)
    await db.commit()
    return summary


async def auto_seed_if_empty() -> dict[str, Any] | None:
    """Seed when the bots table is empty and seed files exist (called from app lifespan)."""
    from app.db.session import SessionLocal

    if not seed_files_exist():
        return None
    async with SessionLocal() as db:
        count = (await db.execute(select(func.count(Bot.id)))).scalar() or 0
        if count:
            return None
        summary = await seed_all(db)
    logger.info(f"auto-seed: {summary}")
    return summary


async def _main(reset: bool) -> None:
    from app.db.session import SessionLocal, drop_db, engine, init_db

    if reset:
        logger.warning("seed: --reset -> dropping all tables")
        await drop_db()
    await init_db()
    async with SessionLocal() as db:
        summary = await seed_all(db)
    await engine.dispose()
    print(json.dumps(summary, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed VoiceFlow bots and datasets from JSON")
    parser.add_argument("--reset", action="store_true", help="drop & recreate all tables before seeding")
    args = parser.parse_args()
    asyncio.run(_main(args.reset))


if __name__ == "__main__":
    main()
