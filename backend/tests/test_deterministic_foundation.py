from __future__ import annotations

import json
from pathlib import Path

import pytest
from artemisVoice.Evals.agents.deterministic import DeterministicConfirmationAgent
from artemisVoice.Evals.agents.protocol import VoiceAgentAdapter
from artemisVoice.Evals.api.app import create_app
from artemisVoice.Evals.causal.locator import CandidateReason, CandidateTurnLocator
from artemisVoice.Evals.causal.replay import (
    CounterfactualReplayEngine,
    Intervention,
    Verdict,
)
from artemisVoice.Evals.domain.models import Scenario
from artemisVoice.Evals.evaluation.judges import (
    JudgeEvidenceError,
    JudgeResult,
    JudgeVerdict,
    deterministic_judges,
    validate_evidence,
)
from artemisVoice.Evals.evaluation.self_validation import (
    ExpectedCause,
    SelfValidationRunner,
    SuiteSplit,
    load_seeded_suite,
)
from artemisVoice.Evals.patches.service import PatchService, PatchStatus
from artemisVoice.Evals.providers.config import (
    ProviderConfigurationError,
    ProviderName,
    ProviderSettings,
)
from artemisVoice.Evals.redteam.mutations import MutationOperator, RedTeamSearch
from artemisVoice.Evals.storage.sqlite import SQLiteTraceStore
from artemisVoice.Evals.tracing.contracts import Layer, RunTrace
from fastapi.testclient import TestClient
from pydantic import ValidationError

SCENARIOS = Path(__file__).resolve().parents[2] / "scenarios"


def load_scenario(name: str) -> Scenario:
    return Scenario.model_validate(json.loads((SCENARIOS / name).read_text(encoding="utf-8")))


def test_healthy_scenario_is_replayable_and_persisted(tmp_path: Path) -> None:
    scenario = load_scenario("healthy_confirmation.json")
    trace = DeterministicConfirmationAgent().run(scenario)

    assert trace.final_database_state == scenario.expected_database_state
    assert {item.layer for item in trace.turns[0].layers} == set(Layer)

    store = SQLiteTraceStore(tmp_path / "traces.db")
    try:
        store.save(trace)
        restored = store.restore_turn(trace.id, 0)
    finally:
        store.close()

    assert restored.database_state_before["confirmation_code"] == ""
    assert restored.database_state_after == scenario.expected_database_state


def test_seeded_stt_confusion_is_visible_in_trace() -> None:
    scenario = load_scenario("stt_confusion_confirmation.json")
    trace = DeterministicConfirmationAgent().run(scenario)
    stt = next(item for item in trace.turns[0].layers if item.layer is Layer.STT)

    assert trace.final_database_state != scenario.expected_database_state
    assert stt.output["final_hypothesis"].endswith("P8Q2.")


@pytest.mark.parametrize(
    ("fault", "expected_layer"),
    [
        ("forced_endpointing_code", Layer.ENDPOINTING),
        ("forced_llm_code", Layer.LLM),
        ("forced_tool_code", Layer.TOOLS),
        ("forced_tts_code", Layer.TTS),
    ],
)
def test_mock_pipeline_seeds_each_non_stt_layer_fault(fault: str, expected_layer: Layer) -> None:
    scenario = load_scenario("healthy_confirmation.json").model_copy(update={fault: "P8Q2"})
    trace = DeterministicConfirmationAgent().run(scenario)

    assert not trace.is_successful()
    layer = next(item for item in trace.turns[0].layers if item.layer is expected_layer)
    assert "P8Q2" in str(layer.output)


@pytest.mark.parametrize(
    ("fault", "intervention"),
    [
        ("forced_endpointing_code", Intervention.ENDPOINTING_ORACLE),
        ("forced_llm_code", Intervention.LLM_ORACLE),
        ("forced_tool_code", Intervention.TOOLS_ORACLE),
        ("forced_tts_code", Intervention.TTS_ORACLE),
    ],
)
def test_each_mock_layer_has_a_proven_counterfactual_arm(fault: str, intervention: Intervention) -> None:
    scenario = load_scenario("healthy_confirmation.json").model_copy(update={fault: "P8Q2"})
    diagnosis = CounterfactualReplayEngine().diagnose(
        DeterministicConfirmationAgent().run(scenario), turn_index=0, intervention=intervention
    )

    assert diagnosis.verdict is Verdict.PROVEN


def test_provider_configuration_keeps_mock_mode_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = ProviderSettings.from_environment()
    settings.validate()
    assert settings.mode is ProviderName.MOCK

    monkeypatch.setenv("ARTEMIS_PROVIDER_MODE", "live")
    with pytest.raises(ProviderConfigurationError, match="not a supported provider"):
        ProviderSettings.from_environment()


def test_trace_rejects_missing_pipeline_layer() -> None:
    scenario = load_scenario("healthy_confirmation.json")
    trace = DeterministicConfirmationAgent().run(scenario)
    invalid_turn = trace.turns[0].model_copy(update={"layers": trace.turns[0].layers[:-1]})

    with pytest.raises(ValidationError, match="missing layer traces"):
        RunTrace.model_validate(
            trace.model_dump() | {"turns": [invalid_turn.model_dump()]}
        )


def test_stt_oracle_is_proven_against_a_control_arm() -> None:
    trace = DeterministicConfirmationAgent().run(load_scenario("stt_confusion_confirmation.json"))
    diagnosis = CounterfactualReplayEngine().diagnose_stt(trace, turn_index=0, trials=5)

    assert diagnosis.control.pass_count == 0
    assert diagnosis.intervention_arm.pass_count == 5
    assert diagnosis.effect_size == 1.0
    assert diagnosis.fisher_exact_p_value == pytest.approx(0.0079365079)
    assert diagnosis.verdict is Verdict.PROVEN


def test_locator_ranks_the_seeded_stt_failure_and_drives_diagnosis() -> None:
    trace = DeterministicConfirmationAgent().run(load_scenario("stt_confusion_confirmation.json"))
    candidates = CandidateTurnLocator().locate(trace)
    diagnosis = CounterfactualReplayEngine().diagnose_first_stt_candidate(trace, trials=5)

    assert candidates[0].turn_index == 0
    assert candidates[0].score == 1.0
    assert CandidateReason.ENTITY_MISMATCH in candidates[0].reasons
    assert diagnosis.verdict is Verdict.PROVEN


def test_locator_leaves_a_healthy_trace_unranked() -> None:
    trace = DeterministicConfirmationAgent().run(load_scenario("healthy_confirmation.json"))

    assert CandidateTurnLocator().locate(trace) == []


def test_diagnosis_evidence_is_persisted_with_its_source_run(tmp_path: Path) -> None:
    trace = DeterministicConfirmationAgent().run(load_scenario("stt_confusion_confirmation.json"))
    diagnosis = CounterfactualReplayEngine().diagnose_stt(trace, turn_index=0, trials=5)
    store = SQLiteTraceStore(tmp_path / "traces.db")
    try:
        store.save(trace)
        store.save_diagnosis(diagnosis)
        restored = store.get_diagnosis(trace.id, 0, "stt_oracle")
    finally:
        store.close()

    assert restored == diagnosis


def test_stt_patch_requires_proof_and_human_approval_before_application() -> None:
    scenario = load_scenario("stt_confusion_confirmation.json")
    base_agent = DeterministicConfirmationAgent()
    diagnosis = CounterfactualReplayEngine().diagnose_first_stt_candidate(base_agent.run(scenario))
    service = PatchService()
    proposal = service.propose_stt_keyword_boost(diagnosis, keyword="B8")

    assert proposal.status is PatchStatus.PENDING
    with pytest.raises(ValueError, match="must approve"):
        service.apply(proposal, base_agent)

    patched_agent = service.apply(service.approve(proposal), base_agent)
    assert patched_agent.run(scenario).final_database_state == scenario.expected_database_state


def test_approved_stt_patch_passes_failure_held_out_and_regression_gate() -> None:
    failing = load_scenario("stt_confusion_confirmation.json")
    held_out = load_scenario("stt_confusion_confirmation_heldout.json")
    healthy = load_scenario("healthy_confirmation.json")
    base_agent = DeterministicConfirmationAgent()
    diagnosis = CounterfactualReplayEngine().diagnose_first_stt_candidate(base_agent.run(failing))
    service = PatchService()
    proposal = service.approve(
        service.propose_stt_keyword_boost(diagnosis, keyword="B8")
    )

    result = service.validate(
        proposal,
        base_agent=base_agent,
        failing=[failing],
        held_out=[held_out],
        regression=[healthy],
    )

    assert result.passed
    assert [(item.name, item.before_pass_rate, item.after_pass_rate) for item in result.sets] == [
        ("failing", 0.0, 1.0),
        ("held_out", 0.0, 1.0),
        ("regression", 1.0, 1.0),
    ]


def test_approved_patch_and_gate_result_are_persisted(tmp_path: Path) -> None:
    failing = load_scenario("stt_confusion_confirmation.json")
    held_out = load_scenario("stt_confusion_confirmation_heldout.json")
    healthy = load_scenario("healthy_confirmation.json")
    base_agent = DeterministicConfirmationAgent()
    diagnosis = CounterfactualReplayEngine().diagnose_first_stt_candidate(base_agent.run(failing))
    service = PatchService()
    proposal = service.approve(service.propose_stt_keyword_boost(diagnosis, keyword="B8"))
    result = service.validate(
        proposal,
        base_agent=base_agent,
        failing=[failing],
        held_out=[held_out],
        regression=[healthy],
    )
    store = SQLiteTraceStore(tmp_path / "traces.db")
    try:
        store.save_patch(proposal)
        store.save_patch_gate_result(result)
        restored_patch = store.get_patch(proposal.id)
        restored_result = store.get_patch_gate_result(proposal.id)
    finally:
        store.close()

    assert restored_patch == proposal
    assert restored_result == result


def test_blind_seeded_suite_reports_attribution_and_false_blame_metrics() -> None:
    suite = load_seeded_suite(Path("seeded_bugs/initial_suite.json"))
    report = SelfValidationRunner().run(suite, split=SuiteSplit.BLIND)

    assert report.metrics.attribution_accuracy == 1.0
    assert report.metrics.false_blame_rate == 0.0
    assert report.metrics.inconclusive_rate == 0.0
    assert report.confusion_matrix == {
        ExpectedCause.STT: {ExpectedCause.STT: 1},
        ExpectedCause.NOT_THE_AGENT: {ExpectedCause.NOT_THE_AGENT: 2},
    }


def test_api_executes_the_full_approved_patch_workflow(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "api.db"))
    failing = json.loads((SCENARIOS / "stt_confusion_confirmation.json").read_text(encoding="utf-8"))
    held_out = json.loads(
        (SCENARIOS / "stt_confusion_confirmation_heldout.json").read_text(encoding="utf-8")
    )
    healthy = json.loads((SCENARIOS / "healthy_confirmation.json").read_text(encoding="utf-8"))

    run = client.post("/runs", json=failing)
    assert run.status_code == 201
    run_id = run.json()["id"]
    diagnosis = client.post(f"/runs/{run_id}/diagnoses/stt", json={"turn_index": 0})
    assert diagnosis.status_code == 201
    assert diagnosis.json()["verdict"] == "proven"
    patch = client.post(
        "/patches/stt-keyword-boost",
        json={"source_run_id": run_id, "turn_index": 0, "keyword": "B8"},
    )
    assert patch.status_code == 201
    approved = client.post(f"/patches/{patch.json()['id']}/approve")
    assert approved.status_code == 200
    result = client.post(
        f"/patches/{patch.json()['id']}/validate",
        json={"failing": [failing], "held_out": [held_out], "regression": [healthy]},
    )

    assert result.status_code == 201
    assert result.json()["passed"] is True
    regression = client.post(f"/patches/{patch.json()['id']}/remember", json=failing)
    assert regression.status_code == 201
    assert client.get("/regressions").json()[0]["scenario"]["id"] == failing["id"]


def test_api_runs_a_non_stt_counterfactual_intervention(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "intervention.db"))
    scenario = json.loads((SCENARIOS / "healthy_confirmation.json").read_text(encoding="utf-8"))
    scenario["forced_tool_code"] = "P8Q2"
    run = client.post("/runs", json=scenario)
    diagnosis = client.post(
        f"/runs/{run.json()['id']}/diagnoses",
        json={"turn_index": 0, "intervention": "tools_oracle"},
    )

    assert diagnosis.status_code == 201
    assert diagnosis.json()["verdict"] == "proven"


def test_api_accepts_a_teammate_agent_adapter(tmp_path: Path) -> None:
    class RecordingAdapter:
        def __init__(self) -> None:
            self.delegate = DeterministicConfirmationAgent()
            self.calls = 0

        @property
        def config_hash(self) -> str:
            return self.delegate.config_hash

        def run(self, scenario: Scenario):
            self.calls += 1
            return self.delegate.run(scenario)

    adapter = RecordingAdapter()
    assert isinstance(adapter, VoiceAgentAdapter)
    client = TestClient(create_app(tmp_path / "adapter.db", agent=adapter))
    response = client.post(
        "/runs",
        json=json.loads((SCENARIOS / "healthy_confirmation.json").read_text(encoding="utf-8")),
    )

    assert response.status_code == 201
    assert adapter.calls == 1


def test_judges_require_trace_backed_evidence_and_queue_human_review(tmp_path: Path) -> None:
    trace = DeterministicConfirmationAgent().run(load_scenario("stt_confusion_confirmation.json"))
    result = deterministic_judges(trace)[0]
    assert result.verdict is JudgeVerdict.FAIL
    with pytest.raises(JudgeEvidenceError, match="does not exist"):
        validate_evidence(
            trace,
            JudgeResult(
                judge="llm",
                metric="faithfulness",
                verdict=JudgeVerdict.FAIL,
                confidence=0.5,
                evidence=["invented quote"],
            ),
        )

    client = TestClient(create_app(tmp_path / "review.db"))
    run = client.post(
        "/runs",
        json=json.loads((SCENARIOS / "stt_confusion_confirmation.json").read_text(encoding="utf-8")),
    )
    review = client.post(
        f"/runs/{run.json()['id']}/reviews",
        json={
            "results": [
                {
                    "judge": "llm-faithfulness",
                    "metric": "faithfulness",
                    "verdict": "abstain",
                    "confidence": 0.4,
                    "evidence": ["P8Q2"],
                }
            ]
        },
    )

    assert review.status_code == 201
    assert client.get("/reviews").json()[0]["id"] == review.json()["id"]


def test_red_team_generates_four_caller_valid_mutations() -> None:
    source = load_scenario("healthy_confirmation.json")
    mutated = RedTeamSearch().generate(source, DeterministicConfirmationAgent())

    assert {item.operator for item in mutated} == set(MutationOperator)
    assert all(item.caller_valid for item in mutated)
    assert all(item.scenario.parent_id == source.id for item in mutated)
    assert all(item.scenario.mutation_history for item in mutated)
