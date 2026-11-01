from __future__ import annotations

import json
from pathlib import Path

import pytest
from artemisVoice.Evals.agents.deterministic import DeterministicConfirmationAgent
from artemisVoice.Evals.causal.locator import CandidateReason, CandidateTurnLocator
from artemisVoice.Evals.causal.replay import CounterfactualReplayEngine, Verdict
from artemisVoice.Evals.domain.models import Scenario
from artemisVoice.Evals.patches.service import PatchService, PatchStatus
from artemisVoice.Evals.storage.sqlite import SQLiteTraceStore
from artemisVoice.Evals.tracing.contracts import Layer, RunTrace
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
