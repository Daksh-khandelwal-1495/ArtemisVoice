from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from artemisVoice.Evals.tracing.contracts import (
    Layer,
    RunTrace,
    TraceSource,
    normalise_text,
)


class CandidateReason(StrEnum):
    ENTITY_MISMATCH = "entity_mismatch"
    FIRST_INCORRECT_STATE = "first_incorrect_state"
    SPOKEN_MISMATCH = "spoken_mismatch"


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

        voiceflow = trace.source is TraceSource.VOICEFLOW
        for turn in trace.turns:
            layers = {layer.layer: layer for layer in turn.layers}
            caller_raw = layers[Layer.CALLER].output.get("intended_text")
            stt_raw = layers[Layer.STT].output.get("final_hypothesis")
            reasons: list[CandidateReason] = []
            evidence: list[str] = []
            score = 0.0

            if voiceflow:
                # VoiceFlow traces carry no hard-coded entity: compare normalised text and
                # judge state only on the oracle fields this turn has already captured.
                text_differs = caller_raw is not None and normalise_text(caller_raw) != normalise_text(stt_raw or "")
                wrong_fields = [
                    key
                    for key, value in expected.items()
                    if key in turn.database_state_after
                    and turn.database_state_after[key] != ""
                    and normalise_text(turn.database_state_after[key]) != normalise_text(value)
                ]
                state_differs = bool(wrong_fields)
            else:
                text_differs = str(caller_raw) != str(stt_raw)
                wrong_fields = []
                state_differs = turn.database_state_after != expected

            if text_differs:
                reasons.append(CandidateReason.ENTITY_MISMATCH)
                evidence.append(f"Caller intended: {caller_raw}")
                evidence.append(f"STT returned: {stt_raw}")
                score += 0.7

            if state_differs:
                reasons.append(CandidateReason.FIRST_INCORRECT_STATE)
                if wrong_fields:
                    evidence.extend(
                        f"{key}: expected {expected[key]!r}, captured {turn.database_state_after[key]!r}"
                        for key in wrong_fields
                    )
                else:
                    evidence.append(
                        "Database state differs from the scenario's expected final state."
                    )
                score += 0.3

            if voiceflow:
                llm_text = layers[Layer.LLM].output.get("response_text")
                spoken = layers[Layer.TTS].output.get("spoken_text")
                if isinstance(llm_text, str) and isinstance(spoken, str) and normalise_text(llm_text) != normalise_text(spoken):
                    reasons.append(CandidateReason.SPOKEN_MISMATCH)
                    evidence.append(f"LLM said: {llm_text}")
                    evidence.append(f"TTS spoke: {spoken}")
                    score += 0.5

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
