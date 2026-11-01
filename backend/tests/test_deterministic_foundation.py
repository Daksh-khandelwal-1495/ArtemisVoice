from __future__ import annotations

import json
from pathlib import Path

import pytest
from artemisVoice.Evals.agents.deterministic import DeterministicConfirmationAgent
from artemisVoice.Evals.causal.replay import CounterfactualReplayEngine, Verdict
from artemisVoice.Evals.domain.models import Scenario
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
