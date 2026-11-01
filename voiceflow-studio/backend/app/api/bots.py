"""Bots, flows, versions, publish, stats."""

from __future__ import annotations

import copy
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import (
    blank_graph,
    bot_detail,
    bot_summary,
    dataset_names,
    get_bot_or_404,
    latest_version,
    next_version_number,
    node_count,
    parse_uuid,
    slugify,
    unique_slug,
)
from app.db.models import Bot, CallSession, CapturedField, FlowVersion, utcnow
from app.db.session import get_db
from app.schemas.flow import FlowGraph, FlowValidationResult
from app.schemas.providers import BotConfig
from app.services.flow_validator import validate_flow

router = APIRouter(tags=["bots"])


class BotCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    slug: str | None = Field(default=None, max_length=80)
    description: str | None = None
    industry: str | None = None
    avatar_color: str | None = None
    language: str | None = None
    template: str | None = "blank"


class BotPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    industry: str | None = None
    avatar_color: str | None = None
    language: str | None = None
    status: Literal["draft", "published", "archived"] | None = None
    config: dict[str, Any] | None = None


class FlowSave(BaseModel):
    graph: FlowGraph
    note: str | None = None


class FlowValidateBody(BaseModel):
    graph: FlowGraph


class PublishBody(BaseModel):
    version_id: str | None = None


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "options":
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


async def _add_version(db: AsyncSession, bot: Bot, graph: dict[str, Any], note: str | None) -> FlowVersion:
    fv = FlowVersion(bot_id=bot.id, version=await next_version_number(db, bot.id), graph=graph, note=note)
    db.add(fv)
    bot.updated_at = utcnow()
    await db.flush()
    return fv


async def _validate(db: AsyncSession, graph: FlowGraph | dict[str, Any]) -> FlowValidationResult:
    return validate_flow(graph, datasets=await dataset_names(db))


# --------------------------------------------------------------------------- bots CRUD


@router.get("/bots")
async def list_bots(
    include_archived: bool = False,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    q = select(Bot).order_by(Bot.created_at.asc(), Bot.name.asc())
    if status:
        q = q.where(Bot.status == status)
    elif not include_archived:
        q = q.where(Bot.status != "archived")
    bots = (await db.execute(q)).scalars().all()
    return [await bot_summary(db, b) for b in bots]


@router.post("/bots", status_code=201)
async def create_bot(body: BotCreate, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    if body.slug:
        slug = slugify(body.slug)
        if (await db.execute(select(Bot.id).where(Bot.slug == slug))).first() is not None:
            raise HTTPException(status_code=409, detail=f"Bot with slug '{slug}' already exists")
    else:
        slug = await unique_slug(db, body.name)

    graph = blank_graph()
    config = BotConfig().model_dump(mode="json")
    template = (body.template or "blank").strip()
    tpl: Bot | None = None
    if template != "blank":
        tpl = (await db.execute(select(Bot).where(Bot.slug == template))).scalar_one_or_none()
        if tpl is None:
            raise HTTPException(status_code=400, detail=f"Template bot '{template}' not found")
        tv = await latest_version(db, tpl.id)
        if tv is not None:
            graph = copy.deepcopy(tv.graph)
        config = copy.deepcopy(tpl.config or config)

    bot = Bot(
        slug=slug,
        name=body.name,
        description=body.description if body.description is not None else (tpl.description if tpl else ""),
        industry=body.industry if body.industry is not None else (tpl.industry if tpl else ""),
        avatar_color=body.avatar_color or (tpl.avatar_color if tpl else "#6366f1"),
        language=body.language or (tpl.language if tpl else "en-IN"),
        status="draft",
        config=config,
    )
    db.add(bot)
    await db.flush()
    await _add_version(db, bot, graph, "Created from template " + template if tpl else "Initial version")
    await db.commit()
    return await bot_detail(db, bot)


@router.get("/bots/{bot_id}")
async def get_bot(bot_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await bot_detail(db, await get_bot_or_404(db, bot_id))


@router.patch("/bots/{bot_id}")
async def patch_bot(bot_id: str, body: BotPatch, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    bot = await get_bot_or_404(db, bot_id)
    data = body.model_dump(exclude_unset=True)
    if "config" in data and data["config"] is not None:
        merged = _deep_merge(bot.config or {}, data.pop("config"))
        try:
            cfg = BotConfig.model_validate(merged)
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors(include_url=False, include_context=False))
        bot.config = cfg.model_dump(mode="json")
    else:
        data.pop("config", None)
    for k, v in data.items():
        if v is not None:
            setattr(bot, k, v)
    bot.updated_at = utcnow()
    await db.commit()
    return await bot_detail(db, bot)


@router.delete("/bots/{bot_id}", status_code=204)
async def delete_bot(bot_id: str, hard: bool = False, db: AsyncSession = Depends(get_db)) -> Response:
    bot = await get_bot_or_404(db, bot_id)
    if hard:
        bot.published_version_id = None
        await db.flush()
        await db.execute(delete(Bot).where(Bot.id == bot.id))
    else:
        bot.status = "archived"
        bot.updated_at = utcnow()
    await db.commit()
    return Response(status_code=204)


@router.post("/bots/{bot_id}/duplicate", status_code=201)
async def duplicate_bot(bot_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    src = await get_bot_or_404(db, bot_id)
    sv = await latest_version(db, src.id)
    bot = Bot(
        slug=await unique_slug(db, f"{src.slug}-copy"),
        name=f"{src.name} (copy)",
        description=src.description,
        industry=src.industry,
        avatar_color=src.avatar_color,
        language=src.language,
        status="draft",
        config=copy.deepcopy(src.config or {}),
    )
    db.add(bot)
    await db.flush()
    await _add_version(db, bot, copy.deepcopy(sv.graph) if sv else blank_graph(), f"Duplicated from {src.slug}")
    await db.commit()
    return await bot_detail(db, bot)


# --------------------------------------------------------------------------- flow & versions


@router.get("/bots/{bot_id}/flow")
async def get_flow(bot_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    bot = await get_bot_or_404(db, bot_id)
    fv = await latest_version(db, bot.id)
    if fv is None:
        raise HTTPException(status_code=404, detail="Bot has no flow")
    return {"version": fv.version, "version_id": str(fv.id), "graph": fv.graph}


@router.put("/bots/{bot_id}/flow")
async def save_flow(bot_id: str, body: FlowSave, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    bot = await get_bot_or_404(db, bot_id)
    validation = await _validate(db, body.graph)
    fv = await _add_version(db, bot, body.graph.model_dump(mode="json"), body.note)
    await db.commit()
    return {"version": fv.version, "version_id": str(fv.id), "validation": validation.model_dump(mode="json")}


@router.post("/bots/{bot_id}/flow/validate")
async def validate_bot_flow(
    bot_id: str, body: FlowValidateBody, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    await get_bot_or_404(db, bot_id)
    return (await _validate(db, body.graph)).model_dump(mode="json")


@router.post("/bots/{bot_id}/publish")
async def publish_bot(
    bot_id: str, body: PublishBody | None = None, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    bot = await get_bot_or_404(db, bot_id)
    if body and body.version_id:
        vid = parse_uuid(body.version_id)
        fv = await db.get(FlowVersion, vid) if vid else None
        if fv is None or fv.bot_id != bot.id:
            raise HTTPException(status_code=404, detail=f"Version '{body.version_id}' not found for this bot")
    else:
        fv = await latest_version(db, bot.id)
        if fv is None:
            raise HTTPException(status_code=404, detail="Bot has no flow to publish")
    try:
        validation = await _validate(db, fv.graph)
    except ValidationError as e:
        raise HTTPException(
            status_code=400,
            detail={"message": "Stored flow is not a valid FlowGraph",
                    "errors": e.errors(include_url=False, include_context=False)},
        )
    if not validation.valid:
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"Version {fv.version} has validation errors and cannot be published",
                "validation": validation.model_dump(mode="json"),
            },
        )
    bot.published_version_id = fv.id
    bot.status = "published"
    bot.updated_at = utcnow()
    await db.commit()
    return await bot_detail(db, bot)


@router.get("/bots/{bot_id}/versions")
async def list_versions(bot_id: str, db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    bot = await get_bot_or_404(db, bot_id)
    rows = (
        await db.execute(select(FlowVersion).where(FlowVersion.bot_id == bot.id).order_by(FlowVersion.version.desc()))
    ).scalars().all()
    return [
        {
            "id": str(v.id),
            "version": v.version,
            "note": v.note,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "is_published": v.id == bot.published_version_id,
            "node_count": node_count(v.graph),
        }
        for v in rows
    ]


@router.get("/bots/{bot_id}/versions/{version_id}")
async def get_version(bot_id: str, version_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Additive: fetch one version's graph (handy for diffing/previewing)."""
    bot = await get_bot_or_404(db, bot_id)
    vid = parse_uuid(version_id)
    fv = await db.get(FlowVersion, vid) if vid else None
    if fv is None or fv.bot_id != bot.id:
        raise HTTPException(status_code=404, detail="Version not found")
    return {
        "id": str(fv.id), "version": fv.version, "note": fv.note, "graph": fv.graph,
        "created_at": fv.created_at.isoformat() if fv.created_at else None,
        "is_published": fv.id == bot.published_version_id,
    }


@router.post("/bots/{bot_id}/versions/{version_id}/restore")
async def restore_version(bot_id: str, version_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    bot = await get_bot_or_404(db, bot_id)
    vid = parse_uuid(version_id)
    src = await db.get(FlowVersion, vid) if vid else None
    if src is None or src.bot_id != bot.id:
        raise HTTPException(status_code=404, detail="Version not found")
    graph = copy.deepcopy(src.graph)
    try:
        validation = (await _validate(db, graph)).model_dump(mode="json")
    except ValidationError:
        validation = None
    fv = await _add_version(db, bot, graph, f"Restored from v{src.version}")
    await db.commit()
    return {"version": fv.version, "version_id": str(fv.id), "validation": validation}


# --------------------------------------------------------------------------- stats


@router.get("/bots/{bot_id}/stats")
async def bot_stats(bot_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    bot = await get_bot_or_404(db, bot_id)
    rows = (
        await db.execute(
            select(CallSession.status, func.count(CallSession.id)).where(CallSession.bot_id == bot.id)
            .group_by(CallSession.status)
        )
    ).all()
    by_status = {s: c for s, c in rows}
    total = sum(by_status.values())
    avg = (
        await db.execute(
            select(func.avg(CallSession.duration_secs)).where(
                CallSession.bot_id == bot.id, CallSession.duration_secs.is_not(None)
            )
        )
    ).scalar()

    # field capture rate = sessions with a valid capture of the field / total sessions
    fields: list[str] = []
    fv = await latest_version(db, bot.id)
    for n in (fv.graph.get("nodes") if fv else None) or []:
        for f in n.get("fields") or []:
            if f.get("name") not in fields:
                fields.append(f["name"])
    cap_rows = (
        await db.execute(
            select(CapturedField.field_name, func.count(func.distinct(CapturedField.session_id)))
            .join(CallSession, CallSession.id == CapturedField.session_id)
            .where(CallSession.bot_id == bot.id, CapturedField.valid.is_(True))
            .group_by(CapturedField.field_name)
        )
    ).all()
    captured = {name: c for name, c in cap_rows}
    for name in captured:
        if name not in fields:
            fields.append(name)
    completed = by_status.get("completed", 0)
    return {
        "total_sessions": total,
        "completed": completed,
        "transferred": by_status.get("transferred", 0),
        "abandoned": by_status.get("abandoned", 0),
        "error": by_status.get("error", 0),
        "active": by_status.get("active", 0),
        "avg_duration_secs": round(float(avg), 2) if avg is not None else None,
        "completion_rate": round(completed / total, 4) if total else 0.0,
        "field_capture_rates": {f: (round(captured.get(f, 0) / total, 4) if total else 0.0) for f in fields},
    }
