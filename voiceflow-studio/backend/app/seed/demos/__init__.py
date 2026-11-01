"""Demo test oracles (+ fault variants) seeded next to the bots (INTEGRATION_CONTRACT §3).

``app/seed/demos/*.json``: ``{bot_slug, base: {oracle fields}, oracles: [{slug, name, ...overrides}]}``.
Oracles are upserted by (bot, slug); missing bots are skipped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

DEMOS_DIR = Path(__file__).resolve().parent
FIELDS = ("name", "description", "caller_turns", "expected_fields", "expected_lookup", "expected_end_node",
          "expected_status", "expected_bot_response", "fault_profile", "tags")


async def seed_demos(db: AsyncSession, demos_dir: Path = DEMOS_DIR) -> list[str]:
    from app.db.models import Bot, TestOracle, utcnow
    from app.replay.faults import FaultProfile

    seeded: list[str] = []
    for path in sorted(demos_dir.glob("*.json")):
        try:
            data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"seed demos: cannot read {path.name}: {e}")
            continue
        bot = (await db.execute(select(Bot).where(Bot.slug == data.get("bot_slug")))).scalar_one_or_none()
        if bot is None:
            continue
        base = dict(data.get("base") or {})
        for item in data.get("oracles") or []:
            spec = {**base, **item}
            if spec.get("fault_profile"):
                FaultProfile.model_validate(spec["fault_profile"])
            o = (await db.execute(select(TestOracle).where(TestOracle.bot_id == bot.id, TestOracle.slug == spec["slug"]))
                 ).scalar_one_or_none()
            if o is None:
                o = TestOracle(bot_id=bot.id, slug=spec["slug"])
                db.add(o)
            for k in FIELDS:
                default = [] if k in ("caller_turns", "tags") else ({} if k == "expected_fields" else None)
                setattr(o, k, spec.get(k, default) if spec.get(k) is not None else default)
            o.name = spec.get("name") or spec["slug"]
            o.description = spec.get("description") or ""
            o.updated_at = utcnow()
            seeded.append(f"{bot.slug}/{spec['slug']}")
        await db.flush()
    return seeded
