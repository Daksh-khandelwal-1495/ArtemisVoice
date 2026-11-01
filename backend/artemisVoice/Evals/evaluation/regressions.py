from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from artemisVoice.Evals.domain.models import Scenario
from artemisVoice.Evals.patches.service import PatchGateResult, PatchProposal


class RegressionCase(BaseModel):
    """A confirmed failure retained for every future evaluator suite run."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    patch_id: UUID
    scenario: Scenario | None = None
    # VoiceFlow regression cases live in Studio as oracles tagged "regression".
    studio_oracle_id: str | None = None
    studio_bot_id: str | None = None
    source_run_id: UUID | None = None
    oracle: dict[str, Any] | None = None


class RegressionService:
    def remember(self, patch: PatchProposal, gate: PatchGateResult, scenario: Scenario) -> RegressionCase:
        if gate.patch_id != patch.id or not gate.passed:
            raise ValueError("only a patch that passed its validation gate can create a regression case")
        return RegressionCase(patch_id=patch.id, scenario=scenario)

    def remember_voiceflow(
        self,
        patch: PatchProposal,
        gate: PatchGateResult,
        *,
        source_run_id: UUID,
        studio_oracle: dict[str, Any],
    ) -> RegressionCase:
        if gate.patch_id != patch.id or not gate.passed:
            raise ValueError("only a patch that passed its validation gate can create a regression case")
        return RegressionCase(
            patch_id=patch.id,
            studio_oracle_id=str(studio_oracle.get("id")) if studio_oracle.get("id") is not None else None,
            studio_bot_id=patch.studio_bot_id,
            source_run_id=source_run_id,
            oracle=studio_oracle,
        )
