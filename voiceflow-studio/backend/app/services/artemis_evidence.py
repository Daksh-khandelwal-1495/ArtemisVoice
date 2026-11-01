"""Build an honest Artemis evidence bundle from a completed VoiceFlow call.

VoiceFlow Studio already persists useful behavioural evidence. It does not yet
record enough information to claim a causally replayable trace (notably caller
audio and a test oracle). This exporter makes that boundary explicit so callers
can inspect a session without treating an incomplete record as a proven diagnosis.
"""

from __future__ import annotations

from typing import Any


def _provider_settings(config: dict[str, Any], role: str) -> dict[str, Any]:
    value = config.get(role) or {}
    return {
        "provider": value.get("provider"),
        "model": value.get("model"),
        "language": value.get("language"),
        "voice": value.get("voice") if role == "tts" else None,
        "keywords": value.get("keywords", []) if role == "stt" else [],
    }


def _event(event: Any) -> dict[str, Any]:
    return {
        "type": event.type,
        "node_id": event.node_id,
        "timestamp": event.ts.isoformat() if event.ts else None,
        "payload": dict(event.payload or {}),
    }


def build_artemis_evidence(
    *,
    session: Any,
    bot: Any,
    flow_version: Any,
    events: list[Any],
    variables: dict[str, Any],
) -> dict[str, Any]:
    """Translate persisted events into per-caller-turn evidence.

    The output is deliberately an evidence bundle, rather than an Artemis
    RunTrace. Converting it into a causal trace is allowed only after the missing
    replay inputs are captured and an expected outcome is supplied by a test case
    or another authoritative system.
    """

    config = dict(session.config_snapshot or {})
    providers = {
        "speech_to_text": _provider_settings(config, "stt"),
        "llm": _provider_settings(config, "llm"),
        "text_to_speech": _provider_settings(config, "tts"),
    }
    vad = dict(config.get("vad") or {})
    turns: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    latest_node = session.current_node

    def complete_turn() -> None:
        nonlocal current
        if current is None:
            return
        current["tools"]["final_variables"] = variables
        turns.append(current)
        current = None

    for row in events:
        payload = dict(row.payload or {})
        if row.type == "node_enter" and row.node_id:
            latest_node = row.node_id

        if row.type == "user_transcript":
            complete_turn()
            transcript = payload.get("text")
            current = {
                "index": len(turns),
                "started_at": row.ts.isoformat() if row.ts else None,
                "node_id": row.node_id or latest_node,
                "caller": {
                    "recorded_transcript": transcript,
                    "audio_artifact": None,
                },
                "endpointing": {
                    "vad_settings": vad,
                    "turn_closed_at": row.ts.isoformat() if row.ts else None,
                },
                "speech_to_text": {
                    "final_hypothesis": transcript,
                    "settings": providers["speech_to_text"],
                    "confidence": None,
                },
                "llm": {
                    "settings": providers["llm"],
                    "bot_responses": [],
                },
                "tools": {
                    "function_calls": [],
                    "lookups": [],
                    "api_calls": [],
                    "validation_errors": [],
                },
                "text_to_speech": {
                    "settings": providers["text_to_speech"],
                    "spoken_text": [],
                    "audio_artifact": None,
                },
            }
            continue

        if current is None:
            continue
        if row.type == "bot_transcript":
            text = payload.get("text")
            if text:
                current["llm"]["bot_responses"].append(text)
                current["text_to_speech"]["spoken_text"].append(text)
        elif row.type == "function_call":
            current["tools"]["function_calls"].append(_event(row))
        elif row.type == "lookup":
            current["tools"]["lookups"].append(_event(row))
        elif row.type == "api_call":
            current["tools"]["api_calls"].append(_event(row))
        elif row.type == "validation_error":
            current["tools"]["validation_errors"].append(_event(row))

    complete_turn()

    missing = [
        "caller audio artifact with an immutable checksum",
        "layer-native STT confidence/interim hypotheses",
        "raw LLM request and response artifacts",
        "TTS audio artifact with an immutable checksum",
        "an expected final state and spoken-response oracle from a test case or authoritative system",
        "a sandbox state-restoration handler for replaying side-effecting tools",
    ]
    if not turns:
        missing.insert(0, "at least one recorded user transcript")

    return {
        "schema_version": "voiceflow-artemis-evidence/v1",
        "source": "voiceflow-studio",
        "replay_eligible": False,
        "replay_blockers": missing,
        "session": {
            "id": str(session.id),
            "bot_id": str(session.bot_id),
            "bot_name": bot.name if bot else None,
            "flow_version_id": str(session.flow_version_id) if session.flow_version_id else None,
            "flow_version": flow_version.version if flow_version else None,
            "mode": session.mode,
            "status": session.status,
            "end_reason": session.end_reason,
            "started_at": session.started_at.isoformat() if session.started_at else None,
            "ended_at": session.ended_at.isoformat() if session.ended_at else None,
            "config_snapshot": config,
        },
        "turns": turns,
    }
