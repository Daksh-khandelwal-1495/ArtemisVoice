"""Patch candidates (INTEGRATION_CONTRACT §3, Phase 6).

A candidate is a new DRAFT flow version + a config copy; it is never published here. Only
``POST .../promote`` with an ``X-Approved-By`` header (sent by Artemis after a human approved
the validated patch) publishes it and applies its config to the bot.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import dataset_names, get_bot_or_404, latest_version, next_version_number, parse_uuid
from app.db.models import FlowVersion, PatchCandidate, utcnow
from app.db.session import get_db
from app.replay.patches import PatchError, PatchSpec, apply_patch
from app.services.flow_validator import validate_flow

router = APIRouter(tags=["patches"])


class CandidateCreate(BaseModel):
    patch: PatchSpec
    base_version_id: str | None = None


def _out(c: PatchCandidate, version: int | None = None, validation: dict | None = None) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "candidate_id": str(c.id),
        "bot_id": str(c.bot_id),
        "base_version_id": str(c.base_version_id) if c.base_version_id else None,
        "candidate_version_id": str(c.candidate_version_id) if c.candidate_version_id else None,
        "candidate_version": version,
        "patch": c.patch,
        "config": c.config,
        "graph_diff": c.graph_diff,
        "status": c.status,
        "approved_by": c.approved_by,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "promoted_at": c.promoted_at.isoformat() if c.promoted_at else None,
        **({"validation": validation} if validation is not None else {}),
    }


async def _candidate(db: AsyncSession, bot, cid: str) -> PatchCandidate:
    uid = parse_uuid(cid)
    c = await db.get(PatchCandidate, uid) if uid else None
    if c is None:  # tolerate the candidate version id
        c = (await db.execute(select(PatchCandidate).where(PatchCandidate.candidate_version_id == uid))
             ).scalars().first() if uid else None
    if c is None or c.bot_id != bot.id:
        raise HTTPException(status_code=404, detail="Patch candidate not found")
    return c


@router.post("/bots/{bot_id}/patch-candidates", status_code=201)
async def create_candidate(bot_id: str, body: CandidateCreate, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    bot = await get_bot_or_404(db, bot_id)
    if body.base_version_id:
        vid = parse_uuid(body.base_version_id)
        base = await db.get(FlowVersion, vid) if vid else None
        if base is None or base.bot_id != bot.id:
            raise HTTPException(status_code=404, detail="base version not found for this bot")
    else:
        base = await db.get(FlowVersion, bot.published_version_id) if bot.published_version_id else None
        base = base or await latest_version(db, bot.id)
        if base is None:
            raise HTTPException(status_code=404, detail="bot has no flow")
    try:
        graph, config, diff = apply_patch(base.graph, bot.config or {}, body.patch)
    except (PatchError, ValueError) as e:
        raise HTTPException(status_code=422, detail=f"patch cannot be applied: {e}")
    fv = FlowVersion(bot_id=bot.id, version=await next_version_number(db, bot.id), graph=graph,
                     note=f"Patch candidate ({body.patch.type}) from v{base.version} - draft, not published")
    db.add(fv)
    await db.flush()
    c = PatchCandidate(bot_id=bot.id, base_version_id=base.id, candidate_version_id=fv.id,
                       patch=body.patch.model_dump(mode="json"), config=config, graph_diff=diff, status="draft")
    db.add(c)
    await db.commit()
    validation = validate_flow(graph, datasets=await dataset_names(db)).model_dump(mode="json")
    return _out(c, fv.version, validation)


@router.get("/bots/{bot_id}/patch-candidates")
async def list_candidates(bot_id: str, db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    bot = await get_bot_or_404(db, bot_id)
    rows = (await db.execute(select(PatchCandidate).where(PatchCandidate.bot_id == bot.id)
                             .order_by(PatchCandidate.created_at.desc()))).scalars().all()
    return [_out(c) for c in rows]


@router.get("/bots/{bot_id}/patch-candidates/{cid}")
async def get_candidate(bot_id: str, cid: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    bot = await get_bot_or_404(db, bot_id)
    return _out(await _candidate(db, bot, cid))


@router.post("/bots/{bot_id}/patch-candidates/{cid}/promote")
async def promote_candidate(bot_id: str, cid: str, db: AsyncSession = Depends(get_db),
                            x_approved_by: str | None = Header(default=None)) -> dict[str, Any]:
    if not x_approved_by or not x_approved_by.strip():
        raise HTTPException(status_code=403, detail="X-Approved-By header (human approver) is required to promote")
    bot = await get_bot_or_404(db, bot_id)
    c = await _candidate(db, bot, cid)
    if c.status == "promoted":
        raise HTTPException(status_code=409, detail="candidate already promoted")
    fv = await db.get(FlowVersion, c.candidate_version_id) if c.candidate_version_id else None
    if fv is None:
        raise HTTPException(status_code=409, detail="candidate version no longer exists")
    validation = validate_flow(fv.graph, datasets=await dataset_names(db))
    if not validation.valid:
        raise HTTPException(status_code=400, detail={"message": "candidate has validation errors",
                                                     "validation": validation.model_dump(mode="json")})
    bot.config = c.config
    bot.published_version_id = fv.id
    bot.status = "published"
    bot.updated_at = utcnow()
    c.status, c.approved_by, c.promoted_at = "promoted", x_approved_by.strip()[:200], utcnow()
    await db.commit()
    return {**_out(c, fv.version), "published_version_id": str(fv.id), "published_version": fv.version}
