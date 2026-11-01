"""Artemis evidence bundle for a VoiceFlow session (``voiceflow-artemis-evidence/v1``).

A human/LLM-readable per-turn view of everything Studio recorded: session + config snapshot,
captured_fields table, events before the first caller turn, per caller turn the transcript,
bot responses, function calls, lookups, api calls, validation errors, field captures,
errors / idle / end events and the variables *as of that turn*, plus whatever the replay-grade
trace (``trace_turn`` events, :mod:`app.tracing`) captured for that turn.

``replay_eligible`` mirrors the trace's honest ``replay_ready``; ``replay_blockers`` is the
trace's precise ``missing`` list. The bundle is never a substitute for the trace itself
(``trace_url``).
"""

from __future__ import annotations

from typing import Any

from app.tracing.builder import config_hash, masker_for
from app.tracing.schema import TRACE_EVENT_TYPES

ORACLE_NOTE = "an expected-outcome test oracle must be bound (POST /api/sessions/{id}/oracle) before a causal diagnosis"


def _provider_settings(config: dict[str, Any], role: str) -> dict[str, Any]:
    value = config.get(role) or {}
    out = {
        "provider": value.get("provider"),
        "model": value.get("model"),
        "language": value.get("language"),
        "voice": value.get("voice") if role == "tts" else None,
        "keywords": value.get("keywords", []) if role == "stt" else [],
    }
    if role == "llm":
        out.update(temperature=value.get("temperature"), max_tokens=value.get("max_tokens"),
                   seed=(value.get("options") or {}).get("seed"))
    if role == "tts":
        out.update(speed=value.get("speed"), sample_rate=value.get("sample_rate"))
    return out


def _event(event: Any) -> dict[str, Any]:
    return {
        "type": event.type,
        "node_id": event.node_id,
        "timestamp": event.ts.isoformat() if event.ts else None,
        "payload": dict(event.payload or {}),
    }


_BUCKETS = {
    "function_call": "function_calls",
    "lookup": "lookups",
    "api_call": "api_calls",
    "validation_error": "validation_errors",
    "field_captured": "field_captures",
    "error": "errors",
    "idle": "idle",
    "end": "end",
}


def build_artemis_evidence(*, bundle: dict[str, Any], trace: dict[str, Any] | None, reveal: bool = False,
                           trace_url: str | None = None) -> dict[str, Any]:
    """``bundle`` from :func:`app.tracing.builder.load_session_bundle`; ``trace`` from
    :func:`app.tracing.builder.build_trace` with the same ``reveal`` flag."""
    session, bot, fv, graph = bundle["session"], bundle["bot"], bundle["flow_version"], bundle["graph"]
    mask = masker_for(bundle, reveal)
    config = dict(session.config_snapshot or {})
    providers = {
        "speech_to_text": _provider_settings(config, "stt"),
        "llm": _provider_settings(config, "llm"),
        "text_to_speech": _provider_settings(config, "tts"),
    }
    vad = dict(config.get("vad") or {})
    trace_turns = {t["index"]: t for t in (trace or {}).get("turns") or [] if isinstance(t.get("index"), int)}

    preamble: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    latest_node = None
    running_vars: dict[str, Any] = {}

    def complete_turn() -> None:
        nonlocal current
        if current is None:
            return
        tt = trace_turns.get(current["index"])
        if tt is not None and isinstance(tt.get("state_after"), dict):
            current["tools"]["variables_after_turn"] = tt["state_after"]
            current["tools"]["variables_source"] = "trace"
        else:
            current["tools"]["variables_after_turn"] = dict(running_vars)
            current["tools"]["variables_source"] = "field_captured_events"
        turns.append(current)
        current = None

    for row in bundle["events"]:
        if row.type in TRACE_EVENT_TYPES:
            continue
        payload = dict(row.payload or {})
        if row.type == "node_enter" and row.node_id:
            latest_node = row.node_id
        if row.type == "field_captured" and payload.get("field"):
            running_vars[payload["field"]] = payload.get("value")

        if row.type == "user_transcript":
            complete_turn()
            transcript = payload.get("text")
            idx = len(turns)
            tt = trace_turns.get(idx) or {}
            layers = tt.get("layers") or {}
            stt_out = (layers.get("speech_to_text") or {}).get("output") or {}
            ep_out = (layers.get("endpointing") or {}).get("output") or {}
            llm_l = layers.get("llm") or {}
            tts_out = (layers.get("text_to_speech") or {}).get("output") or {}
            current = {
                "index": idx,
                "started_at": row.ts.isoformat() if row.ts else None,
                "node_id": row.node_id or latest_node,
                "caller": {
                    "recorded_transcript": transcript,
                    "audio_artifact": ((layers.get("caller") or {}).get("output") or {}).get("audio"),
                },
                "endpointing": {
                    "vad_settings": vad,
                    "turn_closed_at": row.ts.isoformat() if row.ts else None,
                    "speech_start_ms": ep_out.get("speech_start_ms"),
                    "speech_end_ms": ep_out.get("speech_end_ms"),
                    "interrupted_bot": ep_out.get("interrupted_bot"),
                },
                "speech_to_text": {
                    "final_hypothesis": transcript,
                    "trace_final_hypothesis": stt_out.get("final_hypothesis"),
                    "interim_hypotheses": stt_out.get("interim_hypotheses"),
                    "settings": providers["speech_to_text"],
                    "confidence": stt_out.get("confidence"),
                },
                "llm": {
                    "settings": providers["llm"],
                    "bot_responses": [],
                    "context_hash": (llm_l.get("input") or {}).get("context_hash"),
                    "tool_calls": (llm_l.get("output") or {}).get("tool_calls"),
                },
                "tools": {k: [] for k in ("function_calls", "lookups", "api_calls", "validation_errors",
                                          "field_captures", "errors")},
                "text_to_speech": {
                    "settings": providers["text_to_speech"],
                    "spoken_text": [],
                    "audio_artifact": tts_out.get("audio"),
                },
                "events": [],
            }
            continue

        if current is None:
            preamble.append(_event(row))
            continue
        current["events"].append(_event(row))
        if row.type == "bot_transcript":
            text = payload.get("text")
            if text:
                current["llm"]["bot_responses"].append(text)
                current["text_to_speech"]["spoken_text"].append(text)
        elif row.type in _BUCKETS and _BUCKETS[row.type] in current["tools"]:
            current["tools"][_BUCKETS[row.type]].append(_event(row))

    complete_turn()

    blockers = list((trace or {}).get("missing") or ["replay-grade trace unavailable"])
    if not turns:
        blockers.insert(0, "at least one recorded user transcript")
    if not (trace or {}).get("oracle_id"):
        blockers.append(ORACLE_NOTE)
    end_events = [_event(e) for e in bundle["events"] if e.type == "end"]

    out = {
        "schema_version": "voiceflow-artemis-evidence/v1",
        "source": "voiceflow-studio",
        "trace_source": (trace or {}).get("source"),
        "replay_eligible": bool((trace or {}).get("replay_ready")) and bool(turns),
        "replay_blockers": blockers,
        "trace_url": trace_url,
        "masked": mask.active,
        "session": {
            "id": str(session.id),
            "bot_id": str(session.bot_id),
            "bot_slug": bot.slug if bot else None,
            "bot_name": bot.name if bot else None,
            "flow_version_id": str(session.flow_version_id) if session.flow_version_id else None,
            "flow_version": fv.version if fv else None,
            "graph_source": bundle["graph_source"],
            "mode": session.mode,
            "status": session.status,
            "end_reason": session.end_reason,
            "end_node": session.current_node,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            "duration_secs": session.duration_secs,
            "config_snapshot": config,
            "config_hash": config_hash(config, graph),
        },
        "captured_fields": [
            {"node_id": c.node_id, "field_name": c.field_name, "value": c.value, "valid": c.valid,
             "attempts": c.attempts, "sensitive": c.field_name in mask.sensitive,
             "captured_at": c.captured_at.isoformat() if c.captured_at else None}
            for c in bundle["captured"]
        ],
        "preamble": {"events": preamble},
        "turns": turns,
        "final_variables": dict(session.variables or {}),
        "end": {"status": session.status, "end_reason": session.end_reason, "end_node": session.current_node,
                "events": end_events},
    }
    return mask(out)
