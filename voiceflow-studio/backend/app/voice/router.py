"""Voice endpoints: SmallWebRTC signalling per bot + a prompt preview debug aid.

* ``POST  /api/bots/{id}/offer?mode=draft|published`` -> ``{sdp, type, pc_id}``
* ``PATCH /api/bots/{id}/offer``                      -> ICE candidates (trickle)
* ``GET   /api/bots/{id}/preview-prompt?node_id=``    -> compiled prompts + tool schemas

Mirrors pipecat's runner (``pipecat/runner/run.py``) but resolves the bot / flow version
from the database and spawns a :class:`app.voice.bot.Call` per new peer connection.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Bot, FlowVersion
from app.db.session import get_db
from app.schemas.flow import FlowGraph
from app.schemas.providers import BotConfig
from app.voice.compiler import FlowRuntime
from app.voice.providers import MissingProviderKey, check_keys

router = APIRouter(prefix="/api", tags=["voice"])

_handler = None
_calls: dict[str, Any] = {}  # session_id -> Call
_tasks: set[asyncio.Task] = set()


def _get_handler():
    global _handler
    if _handler is None:
        from pipecat.transports.smallwebrtc.connection import IceServer
        from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequestHandler

        ice = [IceServer(urls=u.strip()) for u in os.environ.get("VOICE_ICE_SERVERS", "").split(",") if u.strip()]
        _handler = SmallWebRTCRequestHandler(ice_servers=ice or None)
    return _handler


async def shutdown_voice() -> None:
    """Close all peer connections (call from the app lifespan on shutdown)."""
    global _handler
    if _handler is not None:
        try:
            await _handler.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"error closing webrtc connections: {e}")
    for t in list(_tasks):
        t.cancel()


# --------------------------------------------------------------------------- schemas


class OfferBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    sdp: str
    type: str
    pc_id: str | None = Field(default=None, alias="pc_id")
    restart_pc: bool | None = None
    request_data: dict[str, Any] | None = Field(default=None)
    requestData: dict[str, Any] | None = Field(default=None, exclude=True)

    def data(self) -> dict[str, Any]:
        return self.request_data or self.requestData or {}


class Candidate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    candidate: str = ""
    sdp_mid: str | None = None
    sdp_mline_index: int | None = None
    sdpMid: str | None = None
    sdpMLineIndex: int | None = None


class PatchBody(BaseModel):
    pc_id: str
    candidates: list[Candidate] = Field(default_factory=list)


# --------------------------------------------------------------------------- helpers


def deep_merge(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


async def _load_bot(db: AsyncSession, bot_id: str) -> Bot:
    bot = None
    try:
        bot = await db.get(Bot, uuid.UUID(bot_id))
    except ValueError:
        pass
    if bot is None:
        bot = (await db.execute(select(Bot).where(Bot.slug == bot_id))).scalar_one_or_none()
    if bot is None:
        raise HTTPException(404, detail=f"Bot '{bot_id}' not found")
    return bot


async def _load_version(db: AsyncSession, bot: Bot, mode: str) -> FlowVersion:
    if mode == "published":
        if not bot.published_version_id:
            raise HTTPException(409, detail="This bot has no published version yet. Publish it or test in draft mode.")
        fv = await db.get(FlowVersion, bot.published_version_id)
        if fv is None:
            raise HTTPException(409, detail="Published version not found.")
        return fv
    fv = (
        await db.execute(
            select(FlowVersion).where(FlowVersion.bot_id == bot.id).order_by(FlowVersion.version.desc()).limit(1)
        )
    ).scalar_one_or_none()
    if fv is None:
        raise HTTPException(409, detail="This bot has no flow yet.")
    return fv


def _parse_graph(fv: FlowVersion) -> FlowGraph:
    try:
        return FlowGraph.model_validate(fv.graph)
    except ValidationError as e:
        raise HTTPException(422, detail={"message": "Flow graph is invalid", "errors": e.errors(include_url=False)}) from e


def _parse_config(bot: Bot, override: dict[str, Any] | None) -> BotConfig:
    try:
        return BotConfig.model_validate(deep_merge(bot.config or {}, override))
    except ValidationError as e:
        raise HTTPException(422, detail={"message": "Invalid bot config", "errors": e.errors(include_url=False)}) from e


# --------------------------------------------------------------------------- routes


@router.post("/bots/{bot_id}/offer")
async def offer(
    bot_id: str,
    body: OfferBody,
    mode: str = Query("draft", pattern="^(draft|published)$"),
    db: AsyncSession = Depends(get_db),
):
    from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequest

    handler = _get_handler()
    request = SmallWebRTCRequest(sdp=body.sdp, type=body.type, pc_id=body.pc_id, restart_pc=body.restart_pc,
                                 request_data=body.data())

    # Renegotiation of an existing peer connection: no new bot.
    if body.pc_id and body.pc_id in getattr(handler, "_pcs_map", {}):
        async def _noop(_conn):
            return None

        return await handler.handle_web_request(request=request, webrtc_connection_callback=_noop)

    bot = await _load_bot(db, bot_id)
    fv = await _load_version(db, bot, mode)
    graph = _parse_graph(fv)
    override = (body.data() or {}).get("config_override") or (body.data() or {}).get("configOverride")
    config = _parse_config(bot, override)
    try:
        check_keys(config)
    except MissingProviderKey as e:
        raise HTTPException(503, detail={"message": str(e), "missing": e.missing}) from e

    from app.voice.bot import CallContext, build_call, build_transport

    ctx = CallContext(bot_id=bot.id, bot_name=bot.name, mode=mode, flow_version_id=fv.id, flow_version=fv.version,
                      graph=graph, config=config)
    failure: list[BaseException] = []

    async def on_connection(connection):
        try:
            call = build_call(ctx, build_transport(connection))
        except BaseException as e:  # noqa: BLE001
            failure.append(e)
            raise
        sid = str(ctx.session_id)
        _calls[sid] = call

        async def _run():
            try:
                await call.run()
            finally:
                _calls.pop(sid, None)

        task = asyncio.create_task(_run(), name=f"call-{sid}")
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)

    answer = await handler.handle_web_request(request=request, webrtc_connection_callback=on_connection)
    if failure:
        e = failure[0]
        if isinstance(e, MissingProviderKey):
            raise HTTPException(503, detail={"message": str(e), "missing": e.missing})
        logger.error(f"failed to start bot: {e!r}")
        raise HTTPException(500, detail=f"Failed to start voice bot: {e}")
    if isinstance(answer, dict):
        answer = {**answer, "session_id": str(ctx.session_id)}
    return answer


@router.patch("/bots/{bot_id}/offer")
async def ice_candidates(bot_id: str, body: PatchBody):
    from pipecat.transports.smallwebrtc.request_handler import IceCandidate, SmallWebRTCPatchRequest

    cands = [
        IceCandidate(
            candidate=c.candidate or "",
            sdp_mid=c.sdp_mid if c.sdp_mid is not None else (c.sdpMid or ""),
            sdp_mline_index=c.sdp_mline_index if c.sdp_mline_index is not None else (c.sdpMLineIndex or 0),
        )
        for c in body.candidates
    ]
    await _get_handler().handle_patch_request(SmallWebRTCPatchRequest(pc_id=body.pc_id, candidates=cands))
    return {"status": "success"}


@router.get("/bots/{bot_id}/preview-prompt")
async def preview_prompt(
    bot_id: str,
    node_id: str | None = None,
    mode: str = Query("draft", pattern="^(draft|published)$"),
    db: AsyncSession = Depends(get_db),
):
    bot = await _load_bot(db, bot_id)
    fv = await _load_version(db, bot, mode)
    graph = _parse_graph(fv)
    config = _parse_config(bot, None)
    runtime = FlowRuntime(graph=graph, config=config, bot_name=bot.name, session_id="preview")
    nid = node_id or graph.initial_node
    if nid not in runtime.nodes:
        raise HTTPException(404, detail=f"Node '{nid}' not found in flow version {fv.version}")
    data = runtime.preview(nid)
    data["flow_version"] = fv.version
    return data


@router.get("/voice/calls")
async def active_calls():
    """Live calls handled by this process (debug)."""
    return [
        {"session_id": sid, "bot_id": str(c.ctx.bot_id), "mode": c.ctx.mode,
         "current_node": c.runtime.current_node_id, "status": c.runtime.end_status or "active"}
        for sid, c in _calls.items()
    ]
