from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from artemisVoice.Evals.tracing.contracts import RunTrace


class JudgeVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ABSTAIN = "abstain"


class JudgeResult(BaseModel):
    """A verdict with evidence that must be present in the stored trace."""

    model_config = ConfigDict(frozen=True)

    judge: str
    metric: str
    verdict: JudgeVerdict
    confidence: float = Field(ge=0, le=1)
    evidence: list[str]
    safety_flag: bool = False


class JudgeEvidenceError(ValueError):
    """Raised when a qualitative judge cites text not present in the trace."""


def deterministic_judges(trace: RunTrace) -> list[JudgeResult]:
    """Use exact state comparisons before more subjective qualitative judgments."""

    wrong_fields = [
        field
        for field, expected in trace.expected_database_state.items()
        if trace.final_database_state.get(field) != expected
    ]
    if not wrong_fields:
        return [
            JudgeResult(
                judge="deterministic-state-diff",
                metric="task_success",
                verdict=JudgeVerdict.PASS,
                confidence=1.0,
                evidence=list(trace.final_database_state.values()),
            )
        ]
    evidence = [str(trace.final_database_state[field]) for field in wrong_fields]
    return [
        JudgeResult(
            judge="deterministic-state-diff",
            metric="task_success",
            verdict=JudgeVerdict.FAIL,
            confidence=1.0,
            evidence=evidence,
        )
    ]


def validate_evidence(trace: RunTrace, result: JudgeResult) -> JudgeResult:
    """Reject invented LLM-judge quotes before they influence a diagnosis."""

    serialized_trace = trace.model_dump_json()
    missing = [quote for quote in result.evidence if quote not in serialized_trace]
    if missing:
        raise JudgeEvidenceError(f"judge evidence does not exist in trace: {missing}")
    return result


def needs_human_review(results: list[JudgeResult]) -> bool:
    return any(
        result.safety_flag
        or result.confidence < 0.6
        or result.verdict is JudgeVerdict.ABSTAIN
        for result in results
    )
