"""Shared helpers for API routers: bot lookup, serialization, masking."""

from __future__ import annotations

import re
import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Bot, CallSession, Dataset, FlowVersion
from app.schemas.flow import FlowGraph

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    return s[:80] or "bot"


def parse_uuid(value: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


async def get_bot_or_404(db: AsyncSession, id_or_slug: str) -> Bot:
    uid = parse_uuid(id_or_slug)
    bot = None
    if uid is not None:
        bot = await db.get(Bot, uid)
    if bot is None:
        bot = (await db.execute(select(Bot).where(Bot.slug == id_or_slug))).scalar_one_or_none()
    if bot is None:
        raise HTTPException(status_code=404, detail=f"Bot '{id_or_slug}' not found")
    return bot


async def latest_version(db: AsyncSession, bot_id: uuid.UUID) -> FlowVersion | None:
    q = select(FlowVersion).where(FlowVersion.bot_id == bot_id).order_by(FlowVersion.version.desc()).limit(1)
    return (await db.execute(q)).scalar_one_or_none()


async def next_version_number(db: AsyncSession, bot_id: uuid.UUID) -> int:
    cur = (await db.execute(select(func.max(FlowVersion.version)).where(FlowVersion.bot_id == bot_id))).scalar()
    return (cur or 0) + 1


async def dataset_names(db: AsyncSession) -> set[str]:
    return set((await db.execute(select(Dataset.name))).scalars().all())


async def unique_slug(db: AsyncSession, base: str) -> str:
    base = slugify(base)
    slug, i = base, 2
    while (await db.execute(select(Bot.id).where(Bot.slug == slug))).first() is not None:
        slug = f"{base[:75]}-{i}"
        i += 1
    return slug


def node_count(graph: dict[str, Any] | None) -> int:
    if not graph:
        return 0
    return len(graph.get("nodes") or [])


def blank_graph() -> dict[str, Any]:
    g = FlowGraph.model_validate(
        {
            "initial_node": "start",
            "global_prompt": "You are a friendly, concise voice assistant.",
            "nodes": [
                {
                    "id": "start",
                    "type": "start",
                    "label": "Start",
                    "position": {"x": 0, "y": 0},
                    "task_prompt": "Greet the caller and ask how you can help.",
                    "transitions": [
                        {
                            "id": "t_start_end",
                            "target": "end",
                            "kind": "intent",
                            "condition": "The caller's request is handled or they want to end the call.",
                            "label": "Done",
                        }
                    ],
                },
                {
                    "id": "end",
                    "type": "end",
                    "label": "End",
                    "position": {"x": 0, "y": 200},
                    "say_on_enter": "Thank you for calling. Goodbye!",
                    "end_reason": "completed",
                },
            ],
        }
    )
    return g.model_dump(mode="json")


async def bot_summary(db: AsyncSession, bot: Bot, draft: FlowVersion | None = None) -> dict[str, Any]:
    if draft is None:
        draft = await latest_version(db, bot.id)
    published_version = None
    if bot.published_version_id is not None:
        published_version = (
            await db.execute(select(FlowVersion.version).where(FlowVersion.id == bot.published_version_id))
        ).scalar_one_or_none()
    count, last = (
        await db.execute(
            select(func.count(CallSession.id), func.max(CallSession.started_at)).where(CallSession.bot_id == bot.id)
        )
    ).one()
    return {
        "id": str(bot.id),
        "slug": bot.slug,
        "name": bot.name,
        "description": bot.description or "",
        "industry": bot.industry or "",
        "avatar_color": bot.avatar_color,
        "language": bot.language,
        "status": bot.status,
        "config": bot.config or {},
        "draft_version": draft.version if draft else None,
        "published_version": published_version,
        "published_version_id": str(bot.published_version_id) if bot.published_version_id else None,
        "node_count": node_count(draft.graph if draft else None),
        "session_count": count or 0,
        "last_session_at": last.isoformat() if last else None,
        "created_at": bot.created_at.isoformat() if bot.created_at else None,
        "updated_at": bot.updated_at.isoformat() if bot.updated_at else None,
    }


async def bot_detail(db: AsyncSession, bot: Bot) -> dict[str, Any]:
    draft = await latest_version(db, bot.id)
    out = await bot_summary(db, bot, draft)
    out["graph"] = draft.graph if draft else None
    out["draft_version_id"] = str(draft.id) if draft else None
    return out


# --------------------------------------------------------------------------- masking


def mask_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return "****"
    s = str(value)
    if len(s) <= 4:
        return "*" * len(s)
    return "*" * (len(s) - 4) + s[-4:]


def sensitive_fields(graph: dict[str, Any] | None) -> set[str]:
    out: set[str] = set()
    for n in (graph or {}).get("nodes") or []:
        for f in n.get("fields") or []:
            if f.get("sensitive"):
                out.add(f.get("name"))
    return out


def mask_payload(obj: Any, sensitive: set[str]) -> Any:
    """Recursively mask values keyed by sensitive field names, plus `{field|field_name: x, value: v}`
    shaped dicts."""
    if not sensitive:
        return obj
    if isinstance(obj, dict):
        named = obj.get("field") if isinstance(obj.get("field"), str) else obj.get("field_name")
        out: dict[str, Any] = {}
        for k, v in obj.items():
            if k in sensitive or (k == "value" and named in sensitive):
                out[k] = mask_value(v)
            else:
                out[k] = mask_payload(v, sensitive)
        return out
    if isinstance(obj, list):
        return [mask_payload(v, sensitive) for v in obj]
    return obj
