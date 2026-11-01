from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from artemisVoice.Evals.domain.models import Scenario
from artemisVoice.Evals.patches.service import PatchGateResult, PatchProposal


class RegressionCase(BaseModel):
    """A confirmed failure retained for every future evaluator suite run."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    patch_id: UUID
    scenario: Scenario


class RegressionService:
    def remember(self, patch: PatchProposal, gate: PatchGateResult, scenario: Scenario) -> RegressionCase:
        if gate.patch_id != patch.id or not gate.passed:
            raise ValueError("only a patch that passed its validation gate can create a regression case")
        return RegressionCase(patch_id=patch.id, scenario=scenario)
