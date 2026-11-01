from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from artemisVoice.Evals.agents.protocol import (
    ReplayingVoiceAgentAdapter,
    VoiceAgentAdapter,
)
from artemisVoice.Evals.agents.voiceflow import (
    StudioClient,
    TraceNotReplayableError,
    VoiceFlowAdapter,
    run_trace_from_studio,
)
from artemisVoice.Evals.api.app import create_app
from artemisVoice.Evals.causal.locator import CandidateReason, CandidateTurnLocator
from artemisVoice.Evals.patches.voiceflow import field_pattern
from artemisVoice.Evals.tracing.contracts import TraceSource
from fake_studio import BOT_ID, FLOW_VERSION_ID, HAPPY_ORACLE, FakeStudio, make_trace
from fastapi.testclient import TestClient

STT_SENTENCE = "Replacing only the STT result changed the outcome from failure to success in 5/5 replays."


@pytest.fixture
def studio() -> FakeStudio:
    fake = FakeStudio()
    fake.traces["sess-stt"] = make_trace("sess-stt")
    fake.traces["sess-live"] = make_trace(
        "sess-live", replay_ready=False, missing=["turn 0: caller audio artifact", "turn 1: TTS audio artifact"]
    )
    fake.oracles["oracle-heldout"] = {**HAPPY_ORACLE, "id": "oracle-heldout", "slug": "tata-heldout", "tags": ["held_out"]}
    fake.oracles["oracle-regress"] = {**HAPPY_ORACLE, "id": "oracle-regress", "slug": "tata-regress", "tags": ["regression"]}
    return fake


@pytest.fixture
def api(tmp_path: Path, studio: FakeStudio) -> TestClient:
    return TestClient(create_app(tmp_path / "vf.db", studio=studio.client()))


def import_run(api: TestClient, session_id: str = "sess-stt") -> str:
    response = api.post("/voiceflow/import", json={"session_id": session_id})
    assert response.status_code == 201, response.text
    return response.json()["run_id"]


# --------------------------------------------------------------- contracts / adapter


def test_voiceflow_trace_converts_to_a_failing_voiceflow_run_trace() -> None:
    trace = run_trace_from_studio(make_trace("s1"), HAPPY_ORACLE)

    assert trace.source is TraceSource.VOICEFLOW
    assert trace.studio_session_id == "s1"
    assert trace.expected_spoken_contains == ["411001"]
    assert trace.expected_database_state["__end_node"] == "end_booked"
    assert trace.final_database_state["pincode"] == "41100I"
    assert not trace.is_successful()

    healed = run_trace_from_studio(make_trace("s2", heard_pincode="411001"), HAPPY_ORACLE)
    assert healed.is_successful()  # expected ⊆ final and spoken text contains 411001


def test_voiceflow_locator_does_not_assume_a_confirmation_code() -> None:
    trace = run_trace_from_studio(make_trace("s1"), HAPPY_ORACLE)
    candidates = CandidateTurnLocator().locate(trace)

    assert candidates[0].turn_index == 1
    assert CandidateReason.ENTITY_MISMATCH in candidates[0].reasons
    assert CandidateReason.FIRST_INCORRECT_STATE in candidates[0].reasons
    assert any("pincode" in item for item in candidates[0].evidence)


def test_trace_without_oracle_or_readiness_is_refused() -> None:
    with pytest.raises(TraceNotReplayableError) as error:
        run_trace_from_studio(make_trace("s1", oracle_id=None), None)
    assert any("oracle" in item for item in error.value.missing)

    with pytest.raises(TraceNotReplayableError) as error:
        run_trace_from_studio(make_trace("s1", replay_ready=False, missing=["turn 0: caller audio artifact"]), HAPPY_ORACLE)
    assert error.value.missing == ["turn 0: caller audio artifact"]


def test_voiceflow_adapter_implements_the_agent_protocols(studio: FakeStudio) -> None:
    adapter = VoiceFlowAdapter(studio.client(), bot_id=BOT_ID)
    assert isinstance(adapter, VoiceAgentAdapter)
    assert isinstance(adapter, ReplayingVoiceAgentAdapter)

    run, meta = adapter.import_session("sess-stt")
    assert adapter.config_hash == "b" * 64 == run.agent_config_hash
    assert meta.flow_version_id == FLOW_VERSION_ID

    studio.replay_passes["stt_oracle"] = True
    trials = adapter.replay(run, 1, "stt_oracle", 7, trials=3)
    assert [item.passed for item in trials] == [True, True, True]
    assert studio.replay_calls[-1] == {"session_id": "sess-stt", "intervention": "stt_oracle", "trials": 3, "seed": 7}


# --------------------------------------------------------------- import


def test_import_refuses_a_session_that_is_not_replay_ready(api: TestClient) -> None:
    response = api.post("/voiceflow/import", json={"session_id": "sess-live"})

    assert response.status_code == 422
    assert response.json()["detail"]["missing"] == ["turn 0: caller audio artifact", "turn 1: TTS audio artifact"]
    assert api.get("/voiceflow/runs").json() == []
    refused = api.get("/voiceflow/runs", params={"include_refused": True}).json()
    assert refused[0]["run_id"] is None and refused[0]["replay_ready"] is False


def test_import_is_idempotent_per_session(api: TestClient) -> None:
    first = api.post("/voiceflow/import", json={"session_id": "sess-stt"})
    second = api.post("/voiceflow/import", json={"session_id": "sess-stt"})

    assert first.status_code == second.status_code == 201
    assert first.json()["created"] is True and second.json()["created"] is False
    assert first.json()["run_id"] == second.json()["run_id"]
    runs = api.get("/voiceflow/runs").json()
    assert len(runs) == 1
    assert runs[0]["passed"] is False
    assert runs[0]["studio_session_url"].endswith("/sessions/sess-stt")
    run = api.get(f"/runs/{first.json()['run_id']}").json()
    assert run["source"] == "voiceflow" and run["studio_session_id"] == "sess-stt"


def test_import_of_unknown_session_is_404(api: TestClient) -> None:
    assert api.post("/voiceflow/import", json={"session_id": "nope"}).status_code == 404


# --------------------------------------------------------------- diagnosis


def test_all_arms_diagnosis_proves_stt_when_only_stt_oracle_passes(api: TestClient, studio: FakeStudio) -> None:
    studio.replay_passes["stt_oracle"] = True
    run_id = import_run(api)

    response = api.post(f"/runs/{run_id}/diagnoses/all", json={"trials": 5})

    assert response.status_code == 201, response.text
    report = response.json()
    assert report["verdict"] == "proven"
    assert report["proven_layers"] == ["speech_to_text"]
    assert report["summary"] == STT_SENTENCE
    assert report["turn_index"] == 1
    assert report["control"]["pass_count"] == 0
    arms = {arm["intervention"]: arm for arm in report["arms"]}
    assert set(arms) == {"endpointing_oracle", "stt_oracle", "llm_oracle", "tools_oracle", "tts_oracle"}
    stt = arms["stt_oracle"]
    assert stt["intervention_arm"]["pass_count"] == 5
    assert stt["fisher_exact_p_value"] < 0.05
    assert stt["effect_size"] == 1.0
    assert stt["verdict"] == "proven"
    assert stt["intervention_arm"]["replay_session_ids"][0] == "replay-stt_oracle-0"
    assert all(arms[name]["verdict"] == "inconclusive" for name in arms if name != "stt_oracle")
    # One shared control arm + five oracle arms, five trials each, all through Studio.
    assert [call["intervention"] for call in studio.replay_calls] == [
        "control", "endpointing_oracle", "stt_oracle", "llm_oracle", "tools_oracle", "tts_oracle"
    ]
    assert {call["trials"] for call in studio.replay_calls} == {5}
    assert len(api.get(f"/runs/{run_id}/diagnoses").json()) == 5
    assert api.get(f"/runs/{run_id}/diagnoses/all").json()["id"] == report["id"]
    assert api.get("/diagnoses").json()[0]["id"] == report["id"]


def test_all_arms_diagnosis_reports_a_compound_failure(api: TestClient, studio: FakeStudio) -> None:
    studio.replay_passes.update(stt_oracle=True, endpointing_oracle=True)
    run_id = import_run(api)

    report = api.post(f"/runs/{run_id}/diagnoses/all", json={"trials": 5}).json()

    assert report["verdict"] == "compound"
    assert set(report["proven_layers"]) == {"speech_to_text", "endpointing"}
    assert report["summary"].startswith("Compound failure")


def test_all_arms_diagnosis_reports_not_the_agent_when_control_passes(api: TestClient, studio: FakeStudio) -> None:
    studio.replay_passes["control"] = True
    run_id = import_run(api)

    report = api.post(f"/runs/{run_id}/diagnoses/all", json={"trials": 5}).json()

    assert report["verdict"] == "not_the_agent"
    assert report["proven_layers"] == []


def test_single_intervention_diagnosis_accepts_every_arm_for_voiceflow(api: TestClient, studio: FakeStudio) -> None:
    studio.replay_passes["tts_oracle"] = True
    run_id = import_run(api)

    for intervention in ("endpointing_oracle", "stt_oracle", "llm_oracle", "tools_oracle", "tts_oracle"):
        response = api.post(f"/runs/{run_id}/diagnoses", json={"intervention": intervention, "trials": 5})
        assert response.status_code == 201, response.text
        expected = "proven" if intervention == "tts_oracle" else "inconclusive"
        assert response.json()["verdict"] == expected
    assert api.post(f"/runs/{run_id}/diagnoses", json={"intervention": "stt_oracle", "trials": 11}).status_code == 422


# --------------------------------------------------------------- patches


def proven_stt_run(api: TestClient, studio: FakeStudio) -> str:
    studio.replay_passes["stt_oracle"] = True
    run_id = import_run(api)
    assert api.post(f"/runs/{run_id}/diagnoses/all", json={"trials": 5}).json()["verdict"] == "proven"
    return run_id


def test_patch_requires_a_proven_diagnosis_for_the_layer(api: TestClient, studio: FakeStudio) -> None:
    run_id = proven_stt_run(api, studio)

    response = api.post("/voiceflow/patches", json={"source_run_id": run_id, "layer": "tts"})

    assert response.status_code == 422
    assert "proven" in response.json()["detail"]


def test_proven_stt_patch_validates_approves_promotes_and_is_remembered(api: TestClient, studio: FakeStudio) -> None:
    run_id = proven_stt_run(api, studio)
    studio.simulate_passes.update(
        {
            ("oracle-happy", False): False,
            ("oracle-happy", True): True,
            ("oracle-heldout", False): False,
            ("oracle-heldout", True): True,
            ("oracle-regress", False): True,
            ("oracle-regress", True): True,
        }
    )

    created = api.post("/voiceflow/patches", json={"source_run_id": run_id, "layer": "stt"})
    assert created.status_code == 201, created.text
    patch = created.json()
    assert patch["patch_type"] == "stt_keyterm_boost"
    assert patch["params"]["keywords"] == ["411001"]
    assert patch["studio_candidate_id"] == "cand-1"
    assert studio.candidates["cand-1"] == {
        "bot_id": BOT_ID,
        "patch": {"type": "stt_keyterm_boost", "params": {"keywords": ["411001"], "fields": ["pincode"]}},
        "base_version_id": FLOW_VERSION_ID,
    }

    early = api.post(f"/patches/{patch['id']}/approve", headers={"X-Approved-By": "asha@example.com"})
    assert early.status_code == 409
    assert "validate" in early.json()["detail"]
    assert studio.promotions == []

    gate = api.post(f"/patches/{patch['id']}/validate", json={})
    assert gate.status_code == 201, gate.text
    result = gate.json()
    assert result["passed"] is True
    assert [(item["name"], item["before_pass_rate"], item["after_pass_rate"]) for item in result["sets"]] == [
        ("failing", 0.0, 1.0),
        ("held_out", 0.0, 1.0),
        ("regression", 1.0, 1.0),
    ]
    assert result["sets"][0]["cases"][0]["oracle_id"] == "oracle-happy"
    candidate_calls = [call for call in studio.simulate_calls if call.get("candidate_version_id")]
    assert {call["candidate_version_id"] for call in candidate_calls} == {"cand-1"}
    assert all(call["fault_profile"]["stt"]["replacements"] for call in studio.simulate_calls)

    assert api.post(f"/patches/{patch['id']}/approve").status_code == 422  # no named approver
    approved = api.post(f"/patches/{patch['id']}/approve", headers={"X-Approved-By": "asha@example.com"})
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by"] == "asha@example.com"
    assert studio.promotions == [{"bot_id": BOT_ID, "candidate_id": "cand-1", "approved_by": "asha@example.com"}]

    remembered = api.post(f"/patches/{patch['id']}/remember", json={})
    assert remembered.status_code == 201, remembered.text
    oracle_id = remembered.json()["studio_oracle_id"]
    assert studio.oracles[oracle_id]["tags"] == ["regression"]
    assert studio.oracles[oracle_id]["expected_fields"] == HAPPY_ORACLE["expected_fields"]
    assert api.get("/regressions").json()[0]["studio_oracle_id"] == oracle_id
    listed = api.get(f"/patches/{patch['id']}").json()
    assert listed["patch"]["status"] == "approved" and listed["gate"]["passed"] is True


def test_regression_drop_blocks_the_patch_gate_and_approval(api: TestClient, studio: FakeStudio) -> None:
    run_id = proven_stt_run(api, studio)
    studio.simulate_passes.update(
        {
            ("oracle-happy", True): True,
            ("oracle-heldout", True): True,
            ("oracle-regress", False): True,
            ("oracle-regress", True): False,
        }
    )
    patch = api.post("/voiceflow/patches", json={"source_run_id": run_id, "layer": "stt"}).json()

    result = api.post(f"/patches/{patch['id']}/validate", json={}).json()

    assert result["passed"] is False
    assert result["failure_reason"] == "The regression suite regressed."
    approval = api.post(f"/patches/{patch['id']}/approve", json={"approved_by": "asha@example.com"})
    assert approval.status_code == 409
    assert studio.promotions == []
    assert api.post(f"/patches/{patch['id']}/remember", json={}).status_code == 422


def test_patch_gate_fails_when_failing_case_does_not_improve(api: TestClient, studio: FakeStudio) -> None:
    run_id = proven_stt_run(api, studio)
    patch = api.post("/voiceflow/patches", json={"source_run_id": run_id, "layer": "stt"}).json()

    result = api.post(
        f"/patches/{patch['id']}/validate",
        json={"failing_oracle_ids": ["oracle-happy"], "held_out_oracle_ids": [], "regression_oracle_ids": []},
    ).json()

    assert result["passed"] is False
    assert result["failure_reason"] == "The failing case did not improve."
    assert len(result["warnings"]) == 2


def test_layer_patch_mapping_is_bounded(api: TestClient, studio: FakeStudio) -> None:
    studio.replay_passes.update(llm_oracle=True, endpointing_oracle=True)
    run_id = import_run(api)
    api.post(f"/runs/{run_id}/diagnoses/all", json={"trials": 5})

    llm = api.post("/voiceflow/patches", json={"source_run_id": run_id, "layer": "llm"}).json()
    assert llm["patch_type"] == "stricter_validation"
    assert llm["params"] == {"field": "pincode", "pattern": r"^\d{6}$"}
    assert llm["alternatives"][0]["type"] == "confirmation_readback"

    vad = api.post("/voiceflow/patches", json={"source_run_id": run_id, "layer": "endpointing"}).json()
    assert vad["patch_type"] == "vad_stop_secs" and vad["params"]["stop_secs"] == pytest.approx(1.2)

    readback = api.post(
        "/voiceflow/patches",
        json={"source_run_id": run_id, "layer": "endpointing", "patch_type": "confirmation_readback"},
    )
    assert readback.json()["patch_type"] == "confirmation_readback"
    bad = api.post("/voiceflow/patches", json={"source_run_id": run_id, "layer": "llm", "patch_type": "fallback_route"})
    assert bad.status_code == 422
    assert field_pattern("Asha Sharma") == r"^[A-Za-z][A-Za-z\s'.-]*$"


# --------------------------------------------------------------- readiness / CORS


def test_readiness_reports_studio_database_providers_and_replay_counts(api: TestClient) -> None:
    import_run(api)
    api.post("/voiceflow/import", json={"session_id": "sess-live"})

    body = api.get("/readiness").json()

    assert body["database"]["ok"] is True
    assert body["studio"]["reachable"] is True
    assert body["providers"]["studio"] == {"deepgram": True, "openai": True, "soniox": False}
    assert body["providers"]["artemis"]["valid"] is True
    assert body["replay"] == {"imported_runs": 1, "replay_ready": 1, "refused_imports": 1}
    missing = {row["session_id"]: row["missing"] for row in body["runs"]}
    assert missing["sess-stt"] == []
    assert missing["sess-live"] == ["turn 0: caller audio artifact", "turn 1: TTS audio artifact"]
    assert body["ok"] is True


def test_readiness_reports_an_unreachable_studio(tmp_path: Path) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = StudioClient(
        "http://studio.down", http=httpx.Client(base_url="http://studio.down", transport=httpx.MockTransport(refuse))
    )
    api = TestClient(create_app(tmp_path / "down.db", studio=client))

    body = api.get("/readiness").json()
    assert body["studio"]["reachable"] is False
    assert "unreachable" in body["studio"]["error"]
    assert body["ok"] is False
    assert api.post("/voiceflow/import", json={"session_id": "x"}).status_code == 502


def test_cors_origins_come_from_the_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, studio: FakeStudio) -> None:
    default = TestClient(create_app(tmp_path / "cors.db", studio=studio.client()))
    preflight = {"Access-Control-Request-Method": "GET"}
    assert default.options("/health", headers={"Origin": "http://localhost:3000", **preflight}).status_code == 200

    monkeypatch.setenv("ARTEMIS_CORS_ORIGINS", "http://dash.example")
    custom = TestClient(create_app(tmp_path / "cors2.db", studio=studio.client()))
    assert custom.options("/health", headers={"Origin": "http://dash.example", **preflight}).status_code == 200
    assert custom.options("/health", headers={"Origin": "http://localhost:3000", **preflight}).status_code == 400
