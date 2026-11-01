from __future__ import annotations

from enum import StrEnum
from math import comb
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from artemisVoice.Evals.causal.locator import CandidateTurnLocator
from artemisVoice.Evals.tracing.contracts import (
    Layer,
    RunTrace,
    TraceContractError,
    TurnTrace,
    state_hash,
)


class Intervention(StrEnum):
    """A controlled substitution; control deliberately changes nothing."""

    CONTROL = "control"
    STT_ORACLE = "stt_oracle"
    ENDPOINTING_ORACLE = "endpointing_oracle"
    LLM_ORACLE = "llm_oracle"
    TOOLS_ORACLE = "tools_oracle"
    TTS_ORACLE = "tts_oracle"


class Verdict(StrEnum):
    PROVEN = "proven"
    LIKELY = "likely"
    INCONCLUSIVE = "inconclusive"
    NOT_THE_AGENT = "not_the_agent"


class ArmResult(BaseModel):
    """Repeated evidence for a single replay intervention."""

    model_config = ConfigDict(frozen=True)

    intervention: Intervention
    trials: int = Field(ge=1)
    pass_count: int = Field(ge=0)
    replay_run_ids: list[UUID]

    @property
    def pass_rate(self) -> float:
        return self.pass_count / self.trials


class Diagnosis(BaseModel):
    """A bounded causal conclusion for one turn and one intervention."""

    model_config = ConfigDict(frozen=True)

    source_run_id: UUID
    turn_index: int = Field(ge=0)
    intervention: Intervention
    control: ArmResult
    intervention_arm: ArmResult
    effect_size: float
    fisher_exact_p_value: float
    verdict: Verdict


class CounterfactualReplayEngine:
    """Replay a saved deterministic run with exactly one pipeline layer changed.

    The MVP supports one-turn scenarios. Its important guarantee is preserved: the
    control and intervention use the same saved turn, seed, and configuration; the
    intervention mutates only the STT output and the downstream consequences.
    """

    def replay(
        self,
        source: RunTrace,
        *,
        turn_index: int,
        intervention: Intervention,
    ) -> RunTrace:
        if not source.valid_caller_run:
            raise TraceContractError("invalid caller runs cannot be attributed to the agent")
        if turn_index != len(source.turns) - 1:
            raise TraceContractError("the MVP replay engine supports the final recorded turn only")

        source_turn = source.restore_turn(turn_index)
        if intervention is Intervention.CONTROL:
            replay_turn = source_turn
            final_state = source.final_database_state
        elif intervention in {
            Intervention.STT_ORACLE,
            Intervention.ENDPOINTING_ORACLE,
            Intervention.LLM_ORACLE,
            Intervention.TOOLS_ORACLE,
            Intervention.TTS_ORACLE,
        }:
            replay_turn, final_state = self._replace_layer_with_truth(source, turn_index, intervention)
        else:  # pragma: no cover - keeps future enum additions deliberately explicit.
            raise TraceContractError(f"unsupported intervention: {intervention}")

        return RunTrace(
            schema_version=source.schema_version,
            scenario_id=source.scenario_id,
            agent_config_hash=source.agent_config_hash,
            seed=source.seed,
            valid_caller_run=source.valid_caller_run,
            expected_database_state=source.expected_database_state,
            expected_spoken_text=source.expected_spoken_text,
            final_database_state=final_state,
            turns=[replay_turn],
        )

    def diagnose_stt(self, source: RunTrace, *, turn_index: int, trials: int = 5) -> Diagnosis:
        if trials < 1:
            raise ValueError("trials must be positive")

        control = self._run_arm(source, turn_index, Intervention.CONTROL, trials)
        stt_oracle = self._run_arm(source, turn_index, Intervention.STT_ORACLE, trials)
        effect_size = stt_oracle.pass_rate - control.pass_rate
        p_value = fisher_exact_two_sided(
            stt_oracle.pass_count,
            stt_oracle.trials - stt_oracle.pass_count,
            control.pass_count,
            control.trials - control.pass_count,
        )
        return Diagnosis(
            source_run_id=source.id,
            turn_index=turn_index,
            intervention=Intervention.STT_ORACLE,
            control=control,
            intervention_arm=stt_oracle,
            effect_size=effect_size,
            fisher_exact_p_value=p_value,
            verdict=_verdict(control, effect_size, p_value),
        )

    def diagnose_first_stt_candidate(self, source: RunTrace, *, trials: int = 5) -> Diagnosis:
        """Find the highest-ranked trace mismatch, then test the STT hypothesis."""

        candidates = CandidateTurnLocator().locate(source, limit=1)
        if not candidates:
            raise TraceContractError("no evidence-backed candidate turn was found")
        return self.diagnose_stt(source, turn_index=candidates[0].turn_index, trials=trials)

    def _run_arm(
        self,
        source: RunTrace,
        turn_index: int,
        intervention: Intervention,
        trials: int,
    ) -> ArmResult:
        replayed = [
            self.replay(source, turn_index=turn_index, intervention=intervention)
            for _ in range(trials)
        ]
        return ArmResult(
            intervention=intervention,
            trials=trials,
            pass_count=sum(item.is_successful() for item in replayed),
            replay_run_ids=[item.id for item in replayed],
        )

    def _replace_layer_with_truth(
        self, source: RunTrace, turn_index: int, intervention: Intervention
    ) -> tuple[TurnTrace, dict[str, str]]:
        """Swap only STT evidence, then recompute the deterministic downstream layers."""

        source_turn = source.restore_turn(turn_index)
        caller = next(layer for layer in source_turn.layers if layer.layer is Layer.CALLER)
        true_text = str(caller.output["intended_text"])
        true_code = source.expected_database_state["confirmation_code"]
        repaired_layers = []
        for layer in source_turn.layers:
            if layer.layer is Layer.ENDPOINTING and intervention is Intervention.ENDPOINTING_ORACLE:
                repaired_layers.append(layer.model_copy(update={"output": {**layer.output, "heard_code": true_code}}))
            elif layer.layer is Layer.STT and intervention in {Intervention.STT_ORACLE, Intervention.ENDPOINTING_ORACLE}:
                repaired_layers.append(
                    layer.model_copy(update={"output": {"final_hypothesis": true_text, "confidence": 1.0}})
                )
            elif layer.layer is Layer.LLM and intervention in {Intervention.STT_ORACLE, Intervention.ENDPOINTING_ORACLE, Intervention.LLM_ORACLE}:
                repaired_layers.append(
                    layer.model_copy(
                        update={
                            "input": {"transcript": true_text},
                            "output": {"tool_name": "store_confirmation", "arguments": {"code": true_code}},
                        }
                    )
                )
            elif layer.layer is Layer.TOOLS and intervention is not Intervention.TTS_ORACLE:
                repaired_layers.append(
                    layer.model_copy(
                        update={
                            "input": {"name": "store_confirmation", "arguments": {"code": true_code}},
                            "output": {"database_state": source.expected_database_state},
                        }
                    )
                )
            elif layer.layer is Layer.TTS:
                message = f"I saved confirmation code {true_code}."
                repaired_layers.append(
                    layer.model_copy(update={"input": {"text": message}, "output": {"spoken_text": message}})
                )
            else:
                repaired_layers.append(layer)

        repaired_state = source.final_database_state if intervention is Intervention.TTS_ORACLE else dict(source.expected_database_state)
        repaired_turn = source_turn.model_copy(
            update={
                "database_state_after": repaired_state,
                "database_hash_after": state_hash(repaired_state),
                "layers": repaired_layers,
            }
        )
        # Re-validate the copied turn: model_copy intentionally skips validation.
        return type(source_turn).model_validate(repaired_turn.model_dump()), repaired_state
def _verdict(control: ArmResult, effect_size: float, p_value: float) -> Verdict:
    if control.pass_rate == 1.0:
        return Verdict.NOT_THE_AGENT
    if effect_size >= 0.5 and p_value < 0.05:
        return Verdict.PROVEN
    if effect_size >= 0.3:
        return Verdict.LIKELY
    return Verdict.INCONCLUSIVE


def fisher_exact_two_sided(success_a: int, failure_a: int, success_b: int, failure_b: int) -> float:
    """Two-sided Fisher exact test for small, repeated-trial experiment arms."""

    row_a = success_a + failure_a
    row_b = success_b + failure_b
    total_success = success_a + success_b
    total = row_a + row_b
    if min(success_a, failure_a, success_b, failure_b) < 0 or total == 0:
        raise ValueError("contingency table counts must be non-negative and non-empty")

    def probability(successes_in_a: int) -> float:
        return comb(total_success, successes_in_a) * comb(total - total_success, row_a - successes_in_a) / comb(total, row_a)

    observed = probability(success_a)
    lower = max(0, row_a - (total - total_success))
    upper = min(row_a, total_success)
    probabilities = [probability(value) for value in range(lower, upper + 1)]
    return min(1.0, sum(value for value in probabilities if value <= observed + 1e-12))
