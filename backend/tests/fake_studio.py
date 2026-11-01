"""A tiny in-process VoiceFlow Studio implementing INTEGRATION_CONTRACT §1-§3 for tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any
from uuid import uuid4

from artemisVoice.Evals.agents.voiceflow import StudioClient
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.testclient import TestClient

BOT_ID = "bot-tata"
FLOW_VERSION_ID = "flow-v3"
HAPPY_ORACLE = {
    "id": "oracle-happy",
    "bot_id": BOT_ID,
    "slug": "tata-happy",
    "name": "Tata happy path",
    "caller_turns": ["I want a Nexon", "Pune, 411001"],
    "expected_fields": {"car_model": "Nexon", "city": "Pune", "pincode": "411001"},
    "expected_lookup": {"dataset": "tata_dealers", "match": {"pincode": "411001"}},
    "expected_end_node": "end_booked",
    "expected_status": "completed",
    "expected_bot_response": {"contains": ["411001"]},
    "tags": [],
}


def _artifact(session_id: str, name: str, text: str) -> dict[str, Any]:
    return {
        "uri": f"artifact://{session_id}/{name}",
        "sha256": sha256(text.encode()).hexdigest(),
        "media_type": "text/plain",
        "bytes": len(text),
    }


def _layer(input_: dict[str, Any], output: dict[str, Any], settings: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "started_at": "2026-11-01T10:00:00+00:00",
        "finished_at": "2026-11-01T10:00:01+00:00",
        "input": input_,
        "output": output,
        "settings": settings or {},
    }


def make_trace(
    session_id: str,
    *,
    heard_pincode: str = "41100I",
    replay_ready: bool = True,
    missing: list[str] | None = None,
    oracle_id: str | None = "oracle-happy",
) -> dict[str, Any]:
    """Two caller turns; the STT mishears the pincode in turn 1 (fault profile)."""

    turns_spec = [
        ("collect_model", "I want a Nexon", "I want a Nexon", {}, {"car_model": "Nexon"}, "Great, a Nexon."),
        (
            "collect_location",
            "Pune, 411001",
            f"Pune, {heard_pincode}",
            {"car_model": "Nexon"},
            {"car_model": "Nexon", "city": "Pune", "pincode": heard_pincode},
            f"Booked near Pune {heard_pincode}.",
        ),
    ]
    turns = []
    for index, (node, truth, heard, before, after, reply) in enumerate(turns_spec):
        turns.append(
            {
                "index": index,
                "node_id": node,
                "state_before": before,
                "state_after": after,
                "layers": {
                    "caller": _layer({}, {"intended_text": truth, "audio": _artifact(session_id, f"caller_{index}.txt", truth)}),
                    "endpointing": _layer(
                        {},
                        {"speech_start_ms": 0, "speech_end_ms": 1500, "interrupted_bot": False, "segment_text": truth},
                        {"vad": {"stop_secs": 0.8}},
                    ),
                    "speech_to_text": _layer(
                        {"audio": None},
                        {"interim_hypotheses": [], "final_hypothesis": heard, "confidence": 0.9},
                        {"provider": "deepgram", "model": "nova-3", "keywords": []},
                    ),
                    "llm": _layer(
                        {"context_hash": "a" * 64, "message_count": 3 + index, "node_id": node},
                        {"response_text": reply, "tool_calls": []},
                        {"provider": "openai", "model": "gpt-4.1-mini", "temperature": 0, "seed": 7},
                    ),
                    "tools": _layer({"calls": []}, {"results": [], "state_before": before, "state_after": after}),
                    "text_to_speech": _layer(
                        {"text": reply},
                        {"spoken_text": reply, "audio": _artifact(session_id, f"tts_{index}.txt", reply)},
                        {"provider": "soniox", "voice": "asha"},
                    ),
                },
            }
        )
    return {
        "schema_version": "voiceflow-trace/v1",
        "source": "simulation",
        "session": {
            "id": session_id,
            "bot_id": BOT_ID,
            "bot_slug": "tata-motors",
            "bot_name": "Tata Motors",
            "flow_version_id": FLOW_VERSION_ID,
            "flow_version": 3,
            "mode": "draft",
            "status": "completed",
            "end_reason": "flow_end",
            "started_at": "2026-11-01T10:00:00+00:00",
            "ended_at": "2026-11-01T10:01:00+00:00",
            "config_snapshot": {"stt": {"provider": "deepgram"}},
            "config_hash": "b" * 64,
        },
        "seed": 7,
        "fault_profile": {"stt": {"replacements": [{"from": "411001", "to": "41100I"}], "keyword_protected": True}},
        "oracle_id": oracle_id,
        "replay_ready": replay_ready,
        "missing": missing or [],
        "dataset_snapshot_id": "snap-1",
        "initial_state": {},
        "turns": turns,
        "final_state": turns[-1]["state_after"],
        "end_node": "end_booked",
        "events": [],
    }


@dataclass
class FakeStudio:
    traces: dict[str, dict[str, Any]] = field(default_factory=dict)
    oracles: dict[str, dict[str, Any]] = field(default_factory=lambda: {HAPPY_ORACLE["id"]: dict(HAPPY_ORACLE)})
    # intervention -> passes? (per replay trial)
    replay_passes: dict[str, bool] = field(default_factory=dict)
    # (oracle_id, with_candidate) -> passes?
    simulate_passes: dict[tuple[str, bool], bool] = field(default_factory=dict)
    replay_calls: list[dict[str, Any]] = field(default_factory=list)
    simulate_calls: list[dict[str, Any]] = field(default_factory=list)
    candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    promotions: list[dict[str, Any]] = field(default_factory=list)

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/api/health")
        def health() -> dict[str, Any]:
            return {"ok": True, "db": True, "providers": {"deepgram": True, "openai": True, "soniox": False}}

        @app.get("/api/sessions/{session_id}/trace")
        def trace(session_id: str) -> dict[str, Any]:
            if session_id not in self.traces:
                raise HTTPException(status_code=404, detail="session not found")
            return self.traces[session_id]

        @app.get("/api/oracles/{oracle_id}")
        def oracle(oracle_id: str) -> dict[str, Any]:
            if oracle_id not in self.oracles:
                raise HTTPException(status_code=404, detail="oracle not found")
            return self.oracles[oracle_id]

        @app.get("/api/bots/{bot_id}/oracles")
        def list_oracles(bot_id: str) -> list[dict[str, Any]]:
            return [item for item in self.oracles.values() if item.get("bot_id") == bot_id]

        @app.post("/api/bots/{bot_id}/oracles", status_code=201)
        async def create_oracle(bot_id: str, request: Request) -> dict[str, Any]:
            body = await request.json()
            oracle = {**body, "id": f"oracle-{uuid4().hex[:8]}", "bot_id": bot_id}
            self.oracles[oracle["id"]] = oracle
            return oracle

        @app.post("/api/sessions/{session_id}/replay")
        async def replay(session_id: str, request: Request) -> dict[str, Any]:
            body = await request.json()
            self.replay_calls.append({"session_id": session_id, **body})
            passed = self.replay_passes.get(body["intervention"], False)
            return {
                "session_id": session_id,
                "intervention": body["intervention"],
                "seed": body.get("seed"),
                "trials": [
                    {
                        "trial": trial,
                        "passed": passed,
                        "outcome": {"captured": {"pincode": "411001" if passed else "41100I"}, "end_node": "end_booked", "status": "completed", "spoken_texts": []},
                        "run_trace": None,
                        "replay_session_id": f"replay-{body['intervention']}-{trial}",
                    }
                    for trial in range(body["trials"])
                ],
            }

        @app.post("/api/bots/{bot_id}/simulate")
        async def simulate(bot_id: str, request: Request) -> dict[str, Any]:
            body = await request.json()
            self.simulate_calls.append({"bot_id": bot_id, **body})
            with_candidate = bool(body.get("candidate_version_id"))
            passed = self.simulate_passes.get((body["oracle_id"], with_candidate), False)
            session_id = f"sim-{uuid4().hex[:8]}"
            return {"session_id": session_id, "passed": passed, "outcome": {}, "trace_url": f"/api/sessions/{session_id}/trace"}

        @app.post("/api/bots/{bot_id}/patch-candidates", status_code=201)
        async def candidate(bot_id: str, request: Request) -> dict[str, Any]:
            body = await request.json()
            cid = f"cand-{len(self.candidates) + 1}"
            self.candidates[cid] = {"bot_id": bot_id, **body}
            return {"candidate_version_id": cid, "config": {}, "graph_diff": {"changed": [body["patch"]["type"]]}}

        @app.post("/api/bots/{bot_id}/patch-candidates/{cid}/promote")
        def promote(bot_id: str, cid: str, x_approved_by: str | None = Header(default=None)) -> dict[str, Any]:
            if not x_approved_by:
                raise HTTPException(status_code=403, detail="X-Approved-By required")
            self.promotions.append({"bot_id": bot_id, "candidate_id": cid, "approved_by": x_approved_by})
            return {"published_version_id": f"published-{cid}", "approved_by": x_approved_by}

        return app

    def client(self) -> StudioClient:
        return StudioClient("http://studio.test", http=TestClient(self.app()))
