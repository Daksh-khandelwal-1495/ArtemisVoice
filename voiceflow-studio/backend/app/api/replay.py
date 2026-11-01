"""Replay sandbox API (INTEGRATION_CONTRACT §2-§3): simulate, replay, oracle binding, outcome."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.common import get_bot_or_404, parse_uuid
from app.db.models import CallSession, TestOracle
from app.db.session import get_db
from app.replay.llm import LLMFactory, get_llm_factory
from app.replay.oracle import check_outcome, oracle_dict
from app.replay.sandbox import load_snapshot
from app.replay.service import ReplayError, replay, session_outcome, simulate
from app.voice.providers import MissingProviderKey

router = APIRouter(tags=["replay"])

Intervention = Literal["control", "stt_oracle", "endpointing_oracle", "llm_oracle", "tools_oracle", "tts_oracle"]


class ReplayBody(BaseModel):
    intervention: Intervention = "control"
    trials: int = Field(default=1, ge=1, le=10)
    seed: int | None = Field(default=None, ge=0)
    patch: dict[str, Any] | None = None


class SimulateBody(BaseModel):
    oracle_id: str
    fault_profile: dict[str, Any] | None = None
    mode: Literal["draft", "published"] = "draft"
    seed: int | None = Field(default=None, ge=0)
    candidate_version_id: str | None = None
    patch: dict[str, Any] | None = None
    # Render each turn's caller line and bot reply to WAV with the bot's TTS provider (labelled synthesized).
    synthesize_audio: bool = False
    caller_voice: str | None = None  # TTS voice for the simulated caller (default: a contrasting voice)


class BindOracleBody(BaseModel):
    oracle_id: str
    turn_truths: dict[str, str] | None = None


def _raise(e: Exception) -> None:
    if isinstance(e, ReplayError):
        raise HTTPException(status_code=e.status_code, detail=e.detail)
    if isinstance(e, MissingProviderKey):
        raise HTTPException(status_code=503, detail={"message": str(e), "missing": e.missing})
    raise e


async def _oracle_for_bot(db: AsyncSession, bot_id, oracle_id: str) -> TestOracle:
    from sqlalchemy import select

    oid = parse_uuid(oracle_id)
    o = await db.get(TestOracle, oid) if oid else None
    if o is None:  # allow the slug
        o = (await db.execute(select(TestOracle).where(TestOracle.bot_id == bot_id, TestOracle.slug == oracle_id))
             ).scalar_one_or_none()
    if o is None or o.bot_id != bot_id:
        raise HTTPException(status_code=404, detail=f"Oracle '{oracle_id}' not found for this bot")
    return o


@router.post("/bots/{bot_id}/simulate")
async def simulate_bot(bot_id: str, body: SimulateBody, db: AsyncSession = Depends(get_db),
                       llm_factory: LLMFactory = Depends(get_llm_factory)) -> dict[str, Any]:
    bot = await get_bot_or_404(db, bot_id)
    oracle = await _oracle_for_bot(db, bot.id, body.oracle_id)
    if not oracle.caller_turns:
        raise HTTPException(status_code=422, detail="oracle has no caller_turns to script the caller")
    try:
        return await simulate(db, bot, oracle, llm_factory=llm_factory, fault_profile=body.fault_profile,
                              mode=body.mode, seed=body.seed, candidate_version_id=body.candidate_version_id,
                              patch=body.patch, synthesize_audio=body.synthesize_audio,
                              caller_voice=body.caller_voice)
    except Exception as e:  # noqa: BLE001
        _raise(e)


@router.post("/sessions/{session_id}/replay")
async def replay_session(session_id: str, body: ReplayBody, db: AsyncSession = Depends(get_db),
                         llm_factory: LLMFactory = Depends(get_llm_factory)) -> dict[str, Any]:
    try:
        return await replay(db, session_id, llm_factory=llm_factory, intervention=body.intervention,
                            trials=body.trials, seed=body.seed, patch=body.patch)
    except Exception as e:  # noqa: BLE001
        _raise(e)


async def _session_or_404(db: AsyncSession, session_id: str) -> CallSession:
    sid = parse_uuid(session_id)
    s = await db.get(CallSession, sid) if sid else None
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return s


@router.post("/sessions/{session_id}/oracle")
async def bind_oracle(session_id: str, body: BindOracleBody, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    s = await _session_or_404(db, session_id)
    o = await _oracle_for_bot(db, s.bot_id, body.oracle_id)
    truths = {str(i): t for i, t in enumerate(o.caller_turns or [])}
    for k, v in (body.turn_truths or {}).items():
        try:
            truths[str(int(k))] = str(v)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"turn_truths key '{k}' is not a turn index")
    s.oracle_id = o.id
    s.turn_truths = truths
    await db.commit()
    return {"session_id": str(s.id), "oracle_id": str(o.id), "oracle_slug": o.slug, "turn_truths": truths}


@router.get("/sessions/{session_id}/outcome")
async def get_outcome(session_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """The session's recorded outcome, checked against its bound oracle (if any)."""
    s = await _session_or_404(db, session_id)
    outcome, graph = await session_outcome(db, s)
    o = await db.get(TestOracle, s.oracle_id) if s.oracle_id else None
    check = check_outcome(oracle_dict(o), outcome, graph) if (o is not None and graph is not None) else None
    return {"session_id": str(s.id), "oracle_id": str(o.id) if o else None,
            "passed": check["passed"] if check else None, "checks": check["checks"] if check else None,
            "outcome": outcome}


@router.get("/dataset-snapshots/{snapshot_id}")
async def get_snapshot(snapshot_id: str, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    snap = await load_snapshot(db, snapshot_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="Snapshot not found")
    return {"id": str(snap.id), "created_at": snap.created_at.isoformat() if snap.created_at else None,
            "content_hash": snap.content_hash,
            "datasets": {k: {"row_count": len(v), "rows": v} for k, v in (snap.datasets or {}).items()}}
