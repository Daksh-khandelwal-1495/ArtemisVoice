"""Per-session trace collector (stable API shared by live calls, simulations and replays).

Usage::

    collector = TraceCollector(session_id, recorder, ArtifactStore(), source="simulation")
    collector.record_meta(seed=7, initial_state={...}, dataset_snapshot_id=..., fault_profile=...)
    collector.begin_turn(0, "collect_location", state_before=dict(runtime.vars))
    art = await collector.save_artifact("turn-000-caller.txt", b"Pune, 411001", "text/plain")
    collector.record_layer("caller", started_at=t0, finished_at=t1, input={},
                           output={"intended_text": "Pune, 411001", "audio": art}, settings={})
    ...  # endpointing, speech_to_text, llm, tools, text_to_speech
    collector.end_turn(state_after=dict(runtime.vars))   # -> persists one `trace_turn` event

``recorder`` is a :class:`app.voice.recorder.SessionRecorder` (or anything with
``event(type, node_id, payload, mask=False)``). Trace payloads are stored unmasked so a
replay can restore real state; masking happens when the trace is served
(:func:`app.tracing.builder.build_trace` with ``reveal=False``).
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Literal

from loguru import logger

from app.tracing.artifacts import ArtifactStore
from app.tracing.schema import LAYERS, TRACE_META_EVENT, TRACE_TURN_EVENT, Artifact, TurnTrace


def iso(ts: Any) -> str | None:
    """datetime | epoch seconds | ISO string | None -> ISO-8601 (UTC) string."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    return str(ts)


def _json_safe(obj: Any) -> Any:
    from app.voice.recorder import json_safe

    return json_safe(obj)


class TraceCollector:
    def __init__(self, session_id: Any, recorder: Any, artifacts: ArtifactStore | None,
                 source: Literal["live", "simulation", "replay"] = "live"):
        if source not in ("live", "simulation", "replay"):
            raise ValueError(f"invalid trace source: {source!r}")
        self.session_id = str(session_id)
        self.recorder = recorder
        self.artifacts = artifacts or ArtifactStore()
        self.source = source
        self.turns: list[TurnTrace] = []
        self.meta: dict[str, Any] = {"source": source}
        self._current: TurnTrace | None = None

    # ------------------------------------------------------------------ persistence

    def _persist(self, type_: str, node_id: str | None, payload: dict[str, Any]) -> None:
        if self.recorder is None:
            return
        try:
            self.recorder.event(type_, node_id, payload, mask=False)
        except TypeError:  # recorder without a mask flag
            self.recorder.event(type_, node_id, payload)
        except Exception as e:  # noqa: BLE001 - tracing must never break the caller
            logger.warning(f"[trace {self.session_id}] persist {type_} failed: {e}")

    def record_meta(self, **fields: Any) -> None:
        """Session-level trace data: seed, initial_state, fault_profile, oracle_id,
        dataset_snapshot_id, vad / provider notes... Later calls merge over earlier ones."""
        data = _json_safe({**fields, "source": self.source})
        self.meta.update(data)
        self._persist(TRACE_META_EVENT, None, data)

    # ------------------------------------------------------------------ turns

    @property
    def current(self) -> TurnTrace | None:
        return self._current

    def begin_turn(self, index: int, node_id: str | None, state_before: dict[str, Any]) -> None:
        if self._current is not None:
            logger.warning(f"[trace {self.session_id}] begin_turn({index}) while turn "
                           f"{self._current.get('index')} open; closing it without state_after")
            self._current.setdefault("missing", []).append("turn closed without state_after")
            self.end_turn(None)
        self._current = TurnTrace(index=int(index), node_id=node_id,
                                  state_before=_json_safe(copy.deepcopy(state_before or {})),
                                  layers={}, missing=[])

    def record_layer(self, layer: str, *, started_at: Any = None, finished_at: Any = None,
                     input: dict[str, Any] | None = None, output: dict[str, Any] | None = None,
                     settings: dict[str, Any] | None = None) -> None:
        """Record (or replace) one layer of the open turn."""
        if layer not in LAYERS:
            raise ValueError(f"unknown trace layer {layer!r}; expected one of {LAYERS}")
        if self._current is None:
            logger.warning(f"[trace {self.session_id}] record_layer({layer}) without an open turn; ignored")
            return
        self._current["layers"][layer] = {
            "started_at": iso(started_at),
            "finished_at": iso(finished_at),
            "input": _json_safe(input or {}),
            "output": _json_safe(output or {}),
            "settings": _json_safe(settings or {}),
        }

    def mark_missing(self, note: str) -> None:
        """Record a capture gap on the open turn (it makes the trace non-replayable)."""
        if self._current is not None:
            self._current.setdefault("missing", []).append(str(note))

    async def save_artifact(self, name: str, data: bytes, media_type: str) -> Artifact:
        """Write ``data`` to ``artifacts/<session_id>/<name>``; returns the Artifact dict."""
        return await self.artifacts.save(self.session_id, name, data, media_type)

    def end_turn(self, state_after: dict[str, Any] | None) -> TurnTrace | None:
        """Close the open turn and persist it as one ``trace_turn`` session event."""
        turn = self._current
        if turn is None:
            logger.warning(f"[trace {self.session_id}] end_turn without an open turn; ignored")
            return None
        self._current = None
        if state_after is not None:
            turn["state_after"] = _json_safe(copy.deepcopy(state_after))
        if not turn.get("missing"):
            turn.pop("missing", None)
        turn["source"] = self.source  # type: ignore[typeddict-unknown-key]
        self.turns.append(turn)
        self._persist(TRACE_TURN_EVENT, turn.get("node_id"), dict(turn))
        return turn
