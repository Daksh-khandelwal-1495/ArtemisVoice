"""Assemble the contract §1 trace (``voiceflow-trace/v1``) from persisted session data.

Sources: ``call_sessions`` row, its flow version (fallback: latest version of the bot, for
sensitive-field masking), ``session_events`` (``trace_meta`` + ``trace_turn`` events written
by :class:`app.tracing.collector.TraceCollector`; everything else is the event log) and the
artifact files on disk (checked for existence + sha256).

``replay_ready`` is true only when every caller turn has all six layers with real data and
every referenced artifact verifies. Nothing is inferred to fill a gap: gaps go to ``missing``.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.tracing.artifacts import ArtifactStore
from app.tracing.masking import Masker, collect_sensitive_values
from app.tracing.schema import SCHEMA_VERSION, TRACE_META_EVENT, TRACE_TURN_EVENT, missing_for_turn


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def config_hash(config_snapshot: dict[str, Any] | None, graph: dict[str, Any] | None) -> str:
    """Identity of the system under test: bot config snapshot + flow graph."""
    return canonical_hash({"config": config_snapshot or {}, "graph": graph or {}})


def _sensitive(graph: dict[str, Any] | None) -> set[str]:
    from app.api.common import sensitive_fields

    return sensitive_fields(graph)


async def load_session_bundle(db: AsyncSession, session_id: Any) -> dict[str, Any] | None:
    """Session + bot + flow version (+ graph with latest-version fallback) + events + captured."""
    from app.api.common import latest_version
    from app.db.models import Bot, CallSession, CapturedField, FlowVersion, SessionEvent

    try:
        sid = uuid.UUID(str(session_id))
    except (ValueError, TypeError, AttributeError):
        return None
    s = await db.get(CallSession, sid)
    if s is None:
        return None
    bot = await db.get(Bot, s.bot_id)
    fv = await db.get(FlowVersion, s.flow_version_id) if s.flow_version_id else None
    graph = fv.graph if fv else None
    graph_source = "flow_version" if fv else None
    if graph is None:
        latest = await latest_version(db, s.bot_id)
        if latest is not None:
            graph, graph_source = latest.graph, "latest_version_fallback"
    events = (
        await db.execute(select(SessionEvent).where(SessionEvent.session_id == s.id)
                         .order_by(SessionEvent.ts, SessionEvent.id))
    ).scalars().all()
    captured = (
        await db.execute(select(CapturedField).where(CapturedField.session_id == s.id)
                         .order_by(CapturedField.captured_at))
    ).scalars().all()
    return {"session": s, "bot": bot, "flow_version": fv, "graph": graph, "graph_source": graph_source,
            "events": list(events), "captured": list(captured)}


def masker_for(bundle: dict[str, Any], reveal: bool) -> Masker:
    sensitive = set() if reveal else _sensitive(bundle["graph"])
    if not sensitive:
        return Masker(set())
    values: set[str] = set()
    s = bundle["session"]
    collect_sensitive_values(s.variables or {}, sensitive, values)
    for c in bundle["captured"]:
        if c.field_name in sensitive and c.value is not None and "*" not in str(c.value):
            values.add(str(c.value))
    for e in bundle["events"]:
        collect_sensitive_values(e.payload or {}, sensitive, values)
    return Masker(sensitive, values)


def _graph_has_lookup(graph: dict[str, Any] | None) -> bool:
    return any(n.get("type") == "lookup" for n in (graph or {}).get("nodes") or [])


async def build_trace(db: AsyncSession, session_id: Any, reveal: bool = False, *,
                      artifacts: ArtifactStore | None = None) -> dict[str, Any] | None:
    """Contract §1 trace JSON for a session, or None if the session does not exist."""
    bundle = await load_session_bundle(db, session_id)
    if bundle is None:
        return None
    store = artifacts or ArtifactStore()
    s, bot, fv, graph = bundle["session"], bundle["bot"], bundle["flow_version"], bundle["graph"]

    meta: dict[str, Any] = {}
    turns_by_index: dict[int, dict[str, Any]] = {}
    log: list[dict[str, Any]] = []
    for e in bundle["events"]:
        if e.type == TRACE_META_EVENT:
            meta.update(dict(e.payload or {}))
        elif e.type == TRACE_TURN_EVENT:
            t = dict(e.payload or {})
            if isinstance(t.get("index"), int):
                turns_by_index[t["index"]] = t  # a later record of the same index wins
        else:
            log.append({"ts": _iso(e.ts), "type": e.type, "node_id": e.node_id, "payload": dict(e.payload or {})})
    turns = [turns_by_index[i] for i in sorted(turns_by_index)]

    source = meta.get("source") or ("replay" if s.mode == "replay" else "live")
    missing: list[str] = []
    if not (s.config_snapshot or {}):
        missing.append("session: config snapshot")
    if graph is None:
        missing.append("session: flow version")
    elif bundle["graph_source"] != "flow_version":
        missing.append("session: exact flow version (only the latest version is available)")
    if s.status == "active":
        missing.append("session: still active (not ended)")
    if not isinstance(meta.get("initial_state"), dict):
        missing.append("session: initial state")
    dataset_snapshot_id = meta.get("dataset_snapshot_id") or getattr(s, "dataset_snapshot_id", None)
    if dataset_snapshot_id is None and _graph_has_lookup(graph):
        missing.append("session: dataset snapshot")
    if not turns:
        missing.append("session: no caller turns recorded")
    else:
        expected = 0
        for t in turns:
            while expected < t["index"]:
                missing.append(f"turn {expected}: not recorded")
                expected += 1
            expected = t["index"] + 1
            missing.extend(missing_for_turn(t, artifact_ok=store.verify))

    mask = masker_for(bundle, reveal)
    cfg = s.config_snapshot or {}
    llm_seed = ((cfg.get("llm") or {}).get("options") or {}).get("seed")
    seed = meta.get("seed", llm_seed)
    fault_profile = meta.get("fault_profile", getattr(s, "fault_profile", None))
    oracle_id = meta.get("oracle_id") or getattr(s, "oracle_id", None)

    trace = {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "session": {
            "id": str(s.id),
            "bot_id": str(s.bot_id),
            "bot_slug": bot.slug if bot else None,
            "bot_name": bot.name if bot else None,
            "flow_version_id": str(s.flow_version_id) if s.flow_version_id else None,
            "flow_version": fv.version if fv else None,
            "mode": s.mode,
            "status": s.status,
            "end_reason": s.end_reason,
            "started_at": _iso(s.started_at),
            "ended_at": _iso(s.ended_at),
            "duration_secs": s.duration_secs,
            "config_snapshot": cfg,
            "config_hash": config_hash(cfg, graph),
        },
        "seed": seed,
        "fault_profile": fault_profile,
        "oracle_id": str(oracle_id) if oracle_id else None,
        "replay_ready": not missing,
        "missing": missing,
        "masked": mask.active,
        "dataset_snapshot_id": str(dataset_snapshot_id) if dataset_snapshot_id else None,
        "initial_state": mask(meta.get("initial_state")) if isinstance(meta.get("initial_state"), dict) else None,
        "turns": [mask(t) for t in turns],
        "final_state": mask(dict(s.variables or {})),
        "end_node": s.current_node,
        "captured": [
            mask({"node_id": c.node_id, "field_name": c.field_name, "value": c.value, "valid": c.valid,
                  "attempts": c.attempts, "captured_at": _iso(c.captured_at)})
            for c in bundle["captured"]
        ],
        "events": [mask(e) for e in log],
    }
    extra_meta = {k: v for k, v in meta.items()
                  if k not in {"source", "seed", "fault_profile", "oracle_id", "dataset_snapshot_id", "initial_state"}}
    if extra_meta:
        trace["meta"] = mask(extra_meta)
    return trace
