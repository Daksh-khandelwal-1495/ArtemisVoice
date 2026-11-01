"""Contract §1 trace turns -> Artemis ``RunTrace`` JSON (schema_version "1.0",
root ``backend/artemisVoice/Evals/tracing/contracts.py``).

* six ``LayerTrace`` entries per turn (``layer``, ``input``, ``output``, ``settings``, ``seed``,
  ``started_at``, ``finished_at``), ``artifacts`` = caller + TTS text artifacts (sha256);
* ``database_state_before/after`` = captured fields + ``__end_node`` + ``__status`` as strings,
  hashes = sha256 of sorted ``k=v`` lines joined by ``\\n``;
* ``source="voiceflow"``, ``studio_session_id``, ``expected_spoken_contains``; the final
  turn's TTS ``spoken_text`` is the "final confirmation" the oracle checks.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from app.replay.oracle import END_NODE_KEY, STATUS_KEY, as_text, database_state, expected_contains, expected_state
from app.schemas.flow import FlowGraph

TRACE_SCHEMA_VERSION = "1.0"
LAYERS = ("caller", "endpointing", "speech_to_text", "llm", "tools", "text_to_speech")


def state_hash(state: dict[str, str]) -> str:
    canonical = "\n".join(f"{k}={state[k]}" for k in sorted(state))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _ts(v: Any, default: str) -> str:
    return v if isinstance(v, str) and v else default


def _art(a: Any) -> dict[str, Any] | None:
    if isinstance(a, dict) and isinstance(a.get("sha256"), str) and len(a["sha256"]) == 64 and a.get("uri"):
        return {"uri": a["uri"], "sha256": a["sha256"], "media_type": a.get("media_type") or "text/plain"}
    return None


def _db_state(turn: dict[str, Any], key: str, graph: FlowGraph, fallback: dict[str, str]) -> dict[str, str]:
    tools_out = ((turn.get("layers") or {}).get("tools") or {}).get("output") or {}
    st = tools_out.get(key)
    if isinstance(st, dict):
        return {str(k): as_text(v) for k, v in st.items()}
    vars_ = turn.get("state_after" if key == "database_state" else "state_before")
    if isinstance(vars_, dict):
        return database_state(graph, vars_, end_node=fallback.get(END_NODE_KEY), status=fallback.get(STATUS_KEY))
    return dict(fallback)


def build_run_trace(
    *,
    turns: list[dict[str, Any]],
    graph: FlowGraph,
    oracle: dict[str, Any] | None,
    config_hash: str,
    seed: int,
    studio_session_id: str,
    scenario_id: str | None = None,
    final_state: dict[str, str] | None = None,
    today=None,
) -> dict[str, Any] | None:
    """Return RunTrace JSON, or None when there is no caller turn (RunTrace needs >= 1)."""
    if not turns:
        return None
    now = datetime.now(timezone.utc).isoformat()
    out_turns = []
    prev_after: dict[str, str] = {END_NODE_KEY: "", STATUS_KEY: "active"}
    for pos, t in enumerate(sorted(turns, key=lambda x: x.get("index", 0))):
        layers_in = t.get("layers") or {}
        before = _db_state(t, "database_state_before", graph, prev_after)
        after = _db_state(t, "database_state", graph, prev_after)
        if pos == len(turns) - 1 and final_state is not None:
            after = dict(final_state)
        layer_list = []
        for name in LAYERS:
            lay = layers_in.get(name) or {}
            settings = dict(lay.get("settings") or {})
            started = _ts(lay.get("started_at"), now)
            finished = _ts(lay.get("finished_at"), started)
            if finished < started:
                finished = started
            layer_list.append({
                "layer": name, "input": dict(lay.get("input") or {}), "output": dict(lay.get("output") or {}),
                "settings": settings,
                "seed": settings.get("seed") if isinstance(settings.get("seed"), int) and settings["seed"] >= 0 else None,
                "started_at": started, "finished_at": finished,
            })
        arts = []
        for layer, key in (("caller", "audio"), ("speech_to_text", None), ("text_to_speech", "audio")):
            lay = layers_in.get(layer) or {}
            a = _art((lay.get("output") or {}).get(key)) if key else _art((lay.get("input") or {}).get("audio"))
            if a and a not in arts:
                arts.append(a)
        out_turns.append({
            "index": pos,
            "database_state_before": before, "database_state_after": after,
            "database_hash_before": state_hash(before), "database_hash_after": state_hash(after),
            "artifacts": arts, "layers": layer_list,
        })
        prev_after = after
    oracle = oracle or {}
    contains = expected_contains(oracle)
    return {
        "id": str(uuid.uuid4()),
        "schema_version": TRACE_SCHEMA_VERSION,
        "scenario_id": scenario_id or oracle.get("slug") or f"voiceflow-{studio_session_id}",
        "agent_config_hash": config_hash,
        "seed": max(int(seed or 0), 0),
        "created_at": now,
        "valid_caller_run": True,
        "expected_database_state": expected_state(oracle, graph, today) if oracle else {},
        "expected_spoken_text": " ".join(contains),
        "final_database_state": out_turns[-1]["database_state_after"],
        "turns": out_turns,
        "source": "voiceflow",
        "studio_session_id": studio_session_id,
        "expected_spoken_contains": contains,
    }
