from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from artemisVoice.Evals.agents.deterministic import DeterministicConfirmationAgent
from artemisVoice.Evals.causal.replay import CounterfactualReplayEngine, Verdict
from artemisVoice.Evals.domain.models import Scenario
from artemisVoice.Evals.tracing.contracts import Layer


class ExpectedCause(StrEnum):
    STT = Layer.STT.value
    NOT_THE_AGENT = "not_the_agent"


class SuiteSplit(StrEnum):
    TUNING = "tuning"
    BLIND = "blind"


class SeededCase(BaseModel):
    """A reproducible scenario whose correct attribution is known in advance."""

    model_config = ConfigDict(frozen=True)

    id: str
    split: SuiteSplit
    expected_cause: ExpectedCause
    scenario: Scenario


class CaseResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    expected_cause: ExpectedCause
    predicted_cause: str
    verdict: str


class SelfValidationMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    attribution_accuracy: float
    false_blame_rate: float
    inconclusive_rate: float


class SelfValidationReport(BaseModel):
    """A transparent, split-specific report; targets are never substituted for results."""

    model_config = ConfigDict(frozen=True)

    split: SuiteSplit
    case_results: list[CaseResult]
    metrics: SelfValidationMetrics
    confusion_matrix: dict[str, dict[str, int]]


class SelfValidationRunner:
    """Run seeded cases through the actual locator and causal diagnosis pipeline."""

    def __init__(
        self,
        *,
        agent: DeterministicConfirmationAgent | None = None,
        engine: CounterfactualReplayEngine | None = None,
    ) -> None:
        self._agent = agent or DeterministicConfirmationAgent()
        self._engine = engine or CounterfactualReplayEngine()

    def run(self, cases: list[SeededCase], *, split: SuiteSplit) -> SelfValidationReport:
        selected = [case for case in cases if case.split is split]
        if not selected:
            raise ValueError(f"self-validation suite has no {split} cases")
        results = [self._evaluate(case) for case in selected]
        return SelfValidationReport(
            split=split,
            case_results=results,
            metrics=_metrics(results),
            confusion_matrix=_confusion_matrix(results),
        )

    def _evaluate(self, case: SeededCase) -> CaseResult:
        trace = self._agent.run(case.scenario)
        if not trace.valid_caller_run:
            return CaseResult(
                case_id=case.id,
                expected_cause=case.expected_cause,
                predicted_cause=ExpectedCause.NOT_THE_AGENT,
                verdict=Verdict.NOT_THE_AGENT,
            )
        try:
            diagnosis = self._engine.diagnose_first_stt_candidate(trace)
        except ValueError:
            return CaseResult(
                case_id=case.id,
                expected_cause=case.expected_cause,
                predicted_cause=ExpectedCause.NOT_THE_AGENT,
                verdict=Verdict.NOT_THE_AGENT,
            )
        predicted = ExpectedCause.STT if diagnosis.verdict is Verdict.PROVEN else diagnosis.verdict
        return CaseResult(
            case_id=case.id,
            expected_cause=case.expected_cause,
            predicted_cause=predicted,
            verdict=diagnosis.verdict,
        )


def load_seeded_suite(path: Path) -> list[SeededCase]:
    """Load versioned seeded cases from a repository-owned JSON file."""

    data = json.loads(path.read_text(encoding="utf-8"))
    return [SeededCase.model_validate(item) for item in data["cases"]]


def _metrics(results: list[CaseResult]) -> SelfValidationMetrics:
    attributable = [result for result in results if result.expected_cause is not ExpectedCause.NOT_THE_AGENT]
    not_agent = [result for result in results if result.expected_cause is ExpectedCause.NOT_THE_AGENT]
    correct_attributions = sum(
        result.predicted_cause == result.expected_cause for result in attributable
    )
    false_blames = sum(
        result.predicted_cause != ExpectedCause.NOT_THE_AGENT for result in not_agent
    )
    inconclusive = sum(result.predicted_cause == Verdict.INCONCLUSIVE for result in attributable)
    return SelfValidationMetrics(
        attribution_accuracy=correct_attributions / len(attributable) if attributable else 0.0,
        false_blame_rate=false_blames / len(not_agent) if not_agent else 0.0,
        inconclusive_rate=inconclusive / len(attributable) if attributable else 0.0,
    )


def _confusion_matrix(results: list[CaseResult]) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = {}
    for result in results:
        row = matrix.setdefault(str(result.expected_cause), {})
        row[result.predicted_cause] = row.get(result.predicted_cause, 0) + 1
    return matrix
