from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from artemisVoice.Evals.tracing.contracts import Layer, RunTrace


class CandidateReason(StrEnum):
    ENTITY_MISMATCH = "entity_mismatch"
    FIRST_INCORRECT_STATE = "first_incorrect_state"


class CandidateTurn(BaseModel):
    """A trace turn ranked for cheap intervention triage."""

    model_config = ConfigDict(frozen=True)

    turn_index: int = Field(ge=0)
    score: float = Field(ge=0)
    reasons: list[CandidateReason]
    evidence: list[str]


class CandidateTurnLocator:
    """Rank observable failures before spending trials on counterfactual arms.

    The first version uses the strongest deterministic signals available in the
    MVP: divergence between caller-intended text and the STT final hypothesis,
    plus the first turn that diverges from the expected database state. Timing,
    tool, and judge citations are deliberately additive extensions to this
    contract, not separate ranking systems.
    """

    def locate(self, trace: RunTrace, *, limit: int = 3) -> list[CandidateTurn]:
        if limit < 1:
            raise ValueError("candidate limit must be positive")
        candidates: list[CandidateTurn] = []
        expected = trace.expected_database_state

        for turn in trace.turns:
            layers = {layer.layer: layer for layer in turn.layers}
            caller_text = str(layers[Layer.CALLER].output["intended_text"])
            stt_text = str(layers[Layer.STT].output["final_hypothesis"])
            reasons: list[CandidateReason] = []
            evidence: list[str] = []
            score = 0.0

            if caller_text != stt_text:
                reasons.append(CandidateReason.ENTITY_MISMATCH)
                evidence.append(f"Caller intended: {caller_text}")
                evidence.append(f"STT returned: {stt_text}")
                score += 0.7

            if turn.database_state_after != expected:
                reasons.append(CandidateReason.FIRST_INCORRECT_STATE)
                evidence.append(
                    "Database state differs from the scenario's expected final state."
                )
                score += 0.3

            if reasons:
                candidates.append(
                    CandidateTurn(
                        turn_index=turn.index,
                        score=score,
                        reasons=reasons,
                        evidence=evidence,
                    )
                )

        return sorted(candidates, key=lambda candidate: (-candidate.score, candidate.turn_index))[:limit]
