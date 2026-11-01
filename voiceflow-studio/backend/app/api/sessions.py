"""Call session history."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import get_bot_or_404, latest_version, mask_payload, mask_value, parse_uuid, sensitive_fields
from app.db.models import Bot, CallSession, CapturedField, FlowVersion, SessionEvent
from app.db.session import get_db
from app.services.artemis_evidence import build_artemis_evidence

router = APIRouter(tags=["sessions"])


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _required_fields_by_node(graph: dict[str, Any] | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for n in (graph or {}).get("nodes") or []:
        req = [f["name"] for f in n.get("fields") or [] if f.get("required", True)]
        if req:
            out[n["id"]] = req
    return out


@router.get("/sessions")
async def list_sessions(
    bot_id: str | None = None,
    status: str | None = None,
    mode: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    cond = []
    if bot_id:
        bot = await get_bot_or_404(db, bot_id)
        cond.append(CallSession.bot_id == bot.id)
    if status:
        cond.append(CallSession.status == status)
    if mode:
        cond.append(CallSession.mode == mode)

    total = (await db.execute(select(func.count(CallSession.id)).where(*cond))).scalar() or 0
    rows = (
        await db.execute(
            select(CallSession, Bot.name)
            .join(Bot, Bot.id == CallSession.bot_id)
            .where(*cond)
            .order_by(CallSession.started_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    ids = [s.id for s, _ in rows]

    captured: dict[uuid.UUID, set[str]] = {i: set() for i in ids}
    entered: dict[uuid.UUID, set[str]] = {i: set() for i in ids}
    graphs: dict[uuid.UUID, dict[str, Any]] = {}
    if ids:
        for sid, fname in (
            await db.execute(
                select(CapturedField.session_id, CapturedField.field_name).where(
                    CapturedField.session_id.in_(ids), CapturedField.valid.is_(True)
                )
            )
        ).all():
            captured[sid].add(fname)
        for sid, nid in (
            await db.execute(
                select(SessionEvent.session_id, SessionEvent.node_id).where(
                    SessionEvent.session_id.in_(ids), SessionEvent.type == "node_enter"
                ).distinct()
            )
        ).all():
            if nid:
                entered[sid].add(nid)
        vids = {s.flow_version_id for s, _ in rows if s.flow_version_id}
        if vids:
            for vid, g in (await db.execute(select(FlowVersion.id, FlowVersion.graph).where(FlowVersion.id.in_(vids)))).all():
                graphs[vid] = g

    items = []
    for s, bot_name in rows:
        req_by_node = _required_fields_by_node(graphs.get(s.flow_version_id))
        missing: list[str] = []
        for nid in entered[s.id] | ({s.current_node} if s.current_node else set()):
            for f in req_by_node.get(nid, []):
                if f not in captured[s.id] and f not in missing:
                    missing.append(f)
        items.append(
            {
                "id": str(s.id),
                "bot_id": str(s.bot_id),
                "bot_name": bot_name,
                "status": s.status,
                "end_reason": s.end_reason,
                "mode": s.mode,
                "current_node": s.current_node,
                "started_at": _iso(s.started_at),
                "ended_at": _iso(s.ended_at),
                "duration_secs": s.duration_secs,
                "captured_count": len(captured[s.id]),
                "required_missing": missing,
            }
        )
    return {"items": items, "total": total}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, reveal: bool = False, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    sid = parse_uuid(session_id)
    s = await db.get(CallSession, sid) if sid else None
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")
    bot = await db.get(Bot, s.bot_id)
    fv = await db.get(FlowVersion, s.flow_version_id) if s.flow_version_id else None
    graph = fv.graph if fv else None
    if graph is None:
        latest = await latest_version(db, s.bot_id)
        graph = latest.graph if latest else None
    all_sensitive = sensitive_fields(graph)
    sensitive = set() if reveal else all_sensitive

    events = (
        await db.execute(select(SessionEvent).where(SessionEvent.session_id == s.id).order_by(SessionEvent.ts, SessionEvent.id))
    ).scalars().all()
    captured = (
        await db.execute(
            select(CapturedField).where(CapturedField.session_id == s.id).order_by(CapturedField.captured_at)
        )
    ).scalars().all()

    return {
        "id": str(s.id),
        "bot_id": str(s.bot_id),
        "bot_name": bot.name if bot else None,
        "bot_slug": bot.slug if bot else None,
        "flow_version_id": str(s.flow_version_id) if s.flow_version_id else None,
        "flow_version": fv.version if fv else None,
        "mode": s.mode,
        "status": s.status,
        "end_reason": s.end_reason,
        "current_node": s.current_node,
        "config_snapshot": s.config_snapshot or {},
        "variables": mask_payload(s.variables or {}, sensitive),
        "started_at": _iso(s.started_at),
        "ended_at": _iso(s.ended_at),
        "duration_secs": s.duration_secs,
        "masked": bool(sensitive),
        "events": [
            {"id": str(e.id), "ts": _iso(e.ts), "type": e.type, "node_id": e.node_id,
             "payload": mask_payload(e.payload or {}, sensitive)}
            for e in events
        ],
        "captured": [
            {
                "id": str(c.id),
                "node_id": c.node_id,
                "field_name": c.field_name,
                "value": mask_value(c.value) if c.field_name in sensitive else c.value,
                "valid": c.valid,
                "attempts": c.attempts,
                "sensitive": c.field_name in all_sensitive,
                "captured_at": _iso(c.captured_at),
            }
            for c in captured
        ],
    }


@router.get("/sessions/{session_id}/artemis-evidence")
async def get_artemis_evidence(
    session_id: str, reveal: bool = False, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """Export recorded Studio evidence without overstating replay readiness."""
    sid = parse_uuid(session_id)
    session = await db.get(CallSession, sid) if sid else None
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    bot = await db.get(Bot, session.bot_id)
    flow_version = await db.get(FlowVersion, session.flow_version_id) if session.flow_version_id else None
    graph = flow_version.graph if flow_version else None
    all_sensitive = sensitive_fields(graph)
    sensitive = set() if reveal else all_sensitive
    events = (
        await db.execute(
            select(SessionEvent).where(SessionEvent.session_id == session.id).order_by(SessionEvent.ts, SessionEvent.id)
        )
    ).scalars().all()
    evidence = build_artemis_evidence(
        session=session,
        bot=bot,
        flow_version=flow_version,
        events=events,
        variables=mask_payload(session.variables or {}, sensitive),
    )
    for turn in evidence["turns"]:
        turn["caller"] = mask_payload(turn["caller"], sensitive)
        turn["speech_to_text"] = mask_payload(turn["speech_to_text"], sensitive)
        turn["llm"] = mask_payload(turn["llm"], sensitive)
        turn["tools"] = mask_payload(turn["tools"], sensitive)
        turn["text_to_speech"] = mask_payload(turn["text_to_speech"], sensitive)
    return evidence
