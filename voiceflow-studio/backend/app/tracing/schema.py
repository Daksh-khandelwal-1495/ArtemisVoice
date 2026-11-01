"""Replay-grade trace schema (docs/INTEGRATION_CONTRACT.md §1, ``voiceflow-trace/v1``).

The trace is plain JSON. These TypedDicts document the shape; :mod:`app.tracing.builder`
produces it and :func:`missing_for_turn` decides honest replay readiness.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

SCHEMA_VERSION = "voiceflow-trace/v1"
TRACE_TURN_EVENT = "trace_turn"
TRACE_META_EVENT = "trace_meta"
TRACE_EVENT_TYPES = {TRACE_TURN_EVENT, TRACE_META_EVENT}

Source = Literal["live", "simulation", "replay"]

LAYERS: tuple[str, ...] = ("caller", "endpointing", "speech_to_text", "llm", "tools", "text_to_speech")
LAYER_LABELS = {
    "caller": "caller",
    "endpointing": "endpointing",
    "speech_to_text": "STT",
    "llm": "LLM",
    "tools": "tools",
    "text_to_speech": "TTS",
}


class Artifact(TypedDict):
    uri: str  # artifact://<session_id>/<file>
    sha256: str  # 64 hex
    media_type: str  # audio/wav | text/plain | application/json
    bytes: int


class Layer(TypedDict, total=False):
    started_at: str | None
    finished_at: str | None
    input: dict[str, Any]
    output: dict[str, Any]
    settings: dict[str, Any]


class TurnTrace(TypedDict, total=False):
    index: int
    node_id: str | None
    state_before: dict[str, Any]
    state_after: dict[str, Any]
    layers: dict[str, Layer]
    missing: list[str]  # capture problems noted while recording (never silently filled)


def is_artifact(v: Any) -> bool:
    return (
        isinstance(v, dict)
        and isinstance(v.get("uri"), str)
        and v["uri"].startswith("artifact://")
        and isinstance(v.get("sha256"), str)
        and len(v["sha256"]) == 64
        and isinstance(v.get("media_type"), str)
        and isinstance(v.get("bytes"), int)
    )


def _has(d: dict[str, Any] | None, key: str) -> bool:
    return isinstance(d, dict) and key in d and d[key] is not None


def missing_for_turn(turn: dict[str, Any], *, artifact_ok=None) -> list[str]:
    """Return human-readable gaps that make ``turn`` non-replayable.

    ``artifact_ok(artifact) -> str | None`` optionally verifies an artifact (returns a problem
    description, e.g. "file missing" / "sha256 mismatch", or None when fine).
    """
    i = turn.get("index")
    p = f"turn {i}"
    out: list[str] = []
    layers = turn.get("layers") or {}
    for name in LAYERS:
        if not isinstance(layers.get(name), dict):
            out.append(f"{p}: {LAYER_LABELS[name]} layer not recorded")
    for note in turn.get("missing") or []:
        out.append(note if str(note).startswith(p) else f"{p}: {note}")

    def check_art(layer: str, where: str, key: str, label: str) -> None:
        lay = layers.get(layer)
        if not isinstance(lay, dict):
            return
        art = (lay.get(where) or {}).get(key)
        if not is_artifact(art):
            out.append(f"{p}: {label} artifact")
            return
        if artifact_ok is not None:
            problem = artifact_ok(art)
            if problem:
                out.append(f"{p}: {label} artifact {problem}")

    check_art("caller", "output", "audio", "caller audio")
    check_art("text_to_speech", "output", "audio", "TTS audio")

    ep = layers.get("endpointing")
    if isinstance(ep, dict):
        o = ep.get("output") or {}
        if not (isinstance(o.get("speech_start_ms"), (int, float)) and isinstance(o.get("speech_end_ms"), (int, float))):
            out.append(f"{p}: endpointing speech start/end timing")
    stt = layers.get("speech_to_text")
    if isinstance(stt, dict) and not isinstance((stt.get("output") or {}).get("final_hypothesis"), str):
        out.append(f"{p}: STT final hypothesis")
    llm = layers.get("llm")
    if isinstance(llm, dict):
        if not _has(llm.get("input"), "context_hash"):
            out.append(f"{p}: LLM input context hash")
        if not _has(llm.get("settings"), "model"):
            out.append(f"{p}: LLM model settings")
    tools = layers.get("tools")
    if isinstance(tools, dict):
        o = tools.get("output") or {}
        if not (isinstance(o.get("state_before"), dict) and isinstance(o.get("state_after"), dict)):
            out.append(f"{p}: tools state before/after snapshot")
    tts = layers.get("text_to_speech")
    if isinstance(tts, dict):
        if not isinstance((tts.get("input") or {}).get("text"), str):
            out.append(f"{p}: TTS source text")
        if not isinstance((tts.get("output") or {}).get("spoken_text"), str):
            out.append(f"{p}: TTS spoken text")
    if not isinstance(turn.get("state_before"), dict) or not isinstance(turn.get("state_after"), dict):
        out.append(f"{p}: state before/after")
    return out
