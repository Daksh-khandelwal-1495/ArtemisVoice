from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from math import comb
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, ConfigDict, Field

from artemisVoice.Evals.agents.protocol import ReplayingVoiceAgentAdapter, ReplayTrial
from artemisVoice.Evals.causal.locator import CandidateTurnLocator
from artemisVoice.Evals.tracing.contracts import (
    Layer,
    RunTrace,
    TraceContractError,
    TraceSource,
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


class ReportVerdict(StrEnum):
    """Overall verdict across all oracle arms; compound = at least two arms proven."""

    PROVEN = "proven"
    COMPOUND = "compound"
    LIKELY = "likely"
    INCONCLUSIVE = "inconclusive"
    NOT_THE_AGENT = "not_the_agent"


ORACLE_ARMS: tuple[Intervention, ...] = (
    Intervention.ENDPOINTING_ORACLE,
    Intervention.STT_ORACLE,
    Intervention.LLM_ORACLE,
    Intervention.TOOLS_ORACLE,
    Intervention.TTS_ORACLE,
)

INTERVENTION_LAYER: dict[Intervention, Layer] = {
    Intervention.ENDPOINTING_ORACLE: Layer.ENDPOINTING,
    Intervention.STT_ORACLE: Layer.STT,
    Intervention.LLM_ORACLE: Layer.LLM,
    Intervention.TOOLS_ORACLE: Layer.TOOLS,
    Intervention.TTS_ORACLE: Layer.TTS,
}

_SUBJECT: dict[Intervention, str] = {
    Intervention.ENDPOINTING_ORACLE: "the endpointing decision",
    Intervention.STT_ORACLE: "the STT result",
    Intervention.LLM_ORACLE: "the LLM tool arguments",
    Intervention.TOOLS_ORACLE: "the tool results",
    Intervention.TTS_ORACLE: "the spoken TTS output",
}


class ArmResult(BaseModel):
    """Repeated evidence for a single replay intervention."""

    model_config = ConfigDict(frozen=True)

    intervention: Intervention
    trials: int = Field(ge=1)
    pass_count: int = Field(ge=0)
    replay_run_ids: list[UUID]
    # VoiceFlow arms: Studio replay sessions and their oracle outcomes, for auditing.
    replay_session_ids: list[str] = Field(default_factory=list)
    outcomes: list[dict[str, Any]] = Field(default_factory=list)

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
    layer: Layer | None = None
    summary: str = ""


class CausalReport(BaseModel):
    """Control plus every oracle arm for one run: the full causal diagnosis."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    source_run_id: UUID
    source: TraceSource
    studio_session_id: str | None = None
    turn_index: int = Field(ge=0)
    trials: int = Field(ge=1)
    seed: int = Field(ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    control: ArmResult
    arms: list[Diagnosis]
    verdict: ReportVerdict
    proven_layers: list[Layer]
    summary: str


class CounterfactualReplayEngine:
    """Replay a saved deterministic run with exactly one pipeline layer changed.

    The MVP supports one-turn scenarios. Its important guarantee is preserved: the
    control and intervention use the same saved turn, seed, and configuration; the
    intervention mutates only the STT output and the downstream consequences.

    Runs whose behaviour cannot be recomputed in-process (``source=voiceflow``)
    are delegated to a replaying adapter; the Studio restores the recorded
    state and swaps exactly one layer for its oracle.
    """

    def __init__(self, adapter: ReplayingVoiceAgentAdapter | None = None) -> None:
        self._adapter = adapter

    def _require_adapter(self, source: RunTrace) -> ReplayingVoiceAgentAdapter:
        if self._adapter is None:
            raise TraceContractError(
                f"{source.source} runs are replayed by their agent; no replaying adapter is configured"
            )
        return self._adapter

    def default_turn_index(self, source: RunTrace) -> int:
        if source.source is TraceSource.DETERMINISTIC:
            return len(source.turns) - 1
        candidates = CandidateTurnLocator().locate(source, limit=1)
        return candidates[0].turn_index if candidates else 0

    def replay(
        self,
        source: RunTrace,
        *,
        turn_index: int,
        intervention: Intervention,
    ) -> RunTrace:
        if not source.valid_caller_run:
            raise TraceContractError("invalid caller runs cannot be attributed to the agent")
        if source.source is not TraceSource.DETERMINISTIC:
            trial = self._delegate(source, turn_index, intervention, 1, source.seed)[0]
            if trial.run_trace is None:
                raise TraceContractError("the agent's replay did not return a valid RunTrace")
            return trial.run_trace
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
        return self.diagnose(
            source, turn_index=turn_index, intervention=Intervention.STT_ORACLE, trials=trials
        )

    def diagnose(
        self,
        source: RunTrace,
        *,
        turn_index: int,
        intervention: Intervention,
        trials: int = 5,
    ) -> Diagnosis:
        if trials < 1:
            raise ValueError("trials must be positive")
        if intervention is Intervention.CONTROL:
            raise ValueError("a diagnosis requires a non-control intervention")

        control = self._run_arm(source, turn_index, Intervention.CONTROL, trials)
        return self._compare(source, turn_index, intervention, control, trials)

    def diagnose_all(
        self,
        source: RunTrace,
        *,
        turn_index: int | None = None,
        trials: int = 5,
    ) -> CausalReport:
        """Run one shared control arm and every oracle arm, then summarise the cause."""

        if trials < 1:
            raise ValueError("trials must be positive")
        if turn_index is None:
            turn_index = self.default_turn_index(source)
        source.restore_turn(turn_index)
        control = self._run_arm(source, turn_index, Intervention.CONTROL, trials)
        arms = [
            self._compare(source, turn_index, intervention, control, trials)
            for intervention in ORACLE_ARMS
        ]
        proven = [arm for arm in arms if arm.verdict is Verdict.PROVEN]
        if control.pass_count == control.trials:
            verdict = ReportVerdict.NOT_THE_AGENT
            summary = _not_the_agent_sentence(control)
        elif len(proven) >= 2:
            verdict = ReportVerdict.COMPOUND
            parts = " or ".join(
                f"only {_SUBJECT[arm.intervention]} ({arm.intervention_arm.pass_count}/{arm.intervention_arm.trials})"
                for arm in proven
            )
            summary = (
                f"Compound failure: replacing {parts} each changed the outcome from failure to success; "
                f"control passed {control.pass_count}/{control.trials}."
            )
        elif proven:
            verdict = ReportVerdict.PROVEN
            summary = proven[0].summary
        else:
            likely = [arm for arm in arms if arm.verdict is Verdict.LIKELY]
            if likely:
                best = max(likely, key=lambda arm: arm.effect_size)
                verdict = ReportVerdict.LIKELY
                summary = best.summary
            else:
                verdict = ReportVerdict.INCONCLUSIVE
                summary = (
                    "No single-layer oracle changed the outcome reliably "
                    f"(control passed {control.pass_count}/{control.trials}); the cause is inconclusive."
                )
        return CausalReport(
            source_run_id=source.id,
            source=source.source,
            studio_session_id=source.studio_session_id,
            turn_index=turn_index,
            trials=trials,
            seed=source.seed,
            control=control,
            arms=arms,
            verdict=verdict,
            proven_layers=[INTERVENTION_LAYER[arm.intervention] for arm in proven],
            summary=summary,
        )

    def _compare(
        self,
        source: RunTrace,
        turn_index: int,
        intervention: Intervention,
        control: ArmResult,
        trials: int,
    ) -> Diagnosis:
        intervention_arm = self._run_arm(source, turn_index, intervention, trials)
        effect_size = intervention_arm.pass_rate - control.pass_rate
        p_value = fisher_exact_two_sided(
            intervention_arm.pass_count,
            intervention_arm.trials - intervention_arm.pass_count,
            control.pass_count,
            control.trials - control.pass_count,
        )
        verdict = _verdict(control, effect_size, p_value)
        return Diagnosis(
            source_run_id=source.id,
            turn_index=turn_index,
            intervention=intervention,
            control=control,
            intervention_arm=intervention_arm,
            effect_size=effect_size,
            fisher_exact_p_value=p_value,
            verdict=verdict,
            layer=INTERVENTION_LAYER.get(intervention),
            summary=arm_sentence(intervention, control, intervention_arm, verdict),
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
        if source.source is not TraceSource.DETERMINISTIC:
            if not source.valid_caller_run:
                raise TraceContractError("invalid caller runs cannot be attributed to the agent")
            results = self._delegate(source, turn_index, intervention, trials, source.seed)
            return ArmResult(
                intervention=intervention,
                trials=len(results),
                pass_count=sum(item.passed for item in results),
                replay_run_ids=[_replay_run_id(item) for item in results],
                replay_session_ids=[item.replay_session_id for item in results if item.replay_session_id],
                outcomes=[item.outcome for item in results],
            )
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

    def _delegate(
        self,
        source: RunTrace,
        turn_index: int,
        intervention: Intervention,
        trials: int,
        seed: int,
    ) -> list[ReplayTrial]:
        results = self._require_adapter(source).replay(
            source, turn_index, intervention.value, seed, trials=trials
        )
        if len(results) != trials:
            raise TraceContractError(
                f"the agent returned {len(results)} replay trials for {intervention}; expected {trials}"
            )
        return results

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
def _replay_run_id(trial: ReplayTrial) -> UUID:
    if trial.run_trace is not None:
        return trial.run_trace.id
    if trial.replay_session_id:
        return uuid5(NAMESPACE_URL, f"voiceflow-replay:{trial.replay_session_id}")
    return uuid4()


def _not_the_agent_sentence(control: ArmResult) -> str:
    return (
        f"The unmodified control replay already succeeds in {control.pass_count}/{control.trials} replays, "
        "so the recorded failure is not attributable to the agent."
    )


def arm_sentence(
    intervention: Intervention, control: ArmResult, arm: ArmResult, verdict: Verdict
) -> str:
    """Human-readable, evidence-bound sentence for one oracle arm."""

    subject = _SUBJECT.get(intervention, str(intervention))
    if verdict is Verdict.NOT_THE_AGENT:
        return _not_the_agent_sentence(control)
    if arm.pass_count > control.pass_count:
        if control.pass_count == 0:
            return (
                f"Replacing only {subject} changed the outcome from failure to success "
                f"in {arm.pass_count}/{arm.trials} replays."
            )
        return (
            f"Replacing only {subject} raised the success rate from {control.pass_count}/{control.trials} "
            f"to {arm.pass_count}/{arm.trials} replays."
        )
    return (
        f"Replacing only {subject} did not change the outcome "
        f"({arm.pass_count}/{arm.trials} replays succeeded vs {control.pass_count}/{control.trials} in control)."
    )


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
