from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from artemisVoice.Evals.agents.deterministic import DeterministicConfirmationAgent
from artemisVoice.Evals.causal.replay import Diagnosis, Intervention, Verdict
from artemisVoice.Evals.domain.models import Scenario
from artemisVoice.Evals.tracing.contracts import TraceSource


class PatchStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PatchProposal(BaseModel):
    """A minimal configuration patch tied to its causal evidence."""

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4)
    diagnosis_source_run_id: UUID
    status: PatchStatus = PatchStatus.PENDING
    patch_type: str
    stt_keyword_boosts: list[str] = Field(default_factory=list)
    # Generalised bounded patch (INTEGRATION_CONTRACT §3 PatchSpec = {type, params}).
    source: TraceSource = TraceSource.DETERMINISTIC
    layer: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    diagnosis_report_id: UUID | None = None
    studio_session_id: str | None = None
    studio_bot_id: str | None = None
    studio_base_version_id: str | None = None
    studio_candidate_id: str | None = None
    studio_graph_diff: Any = None
    approved_by: str | None = None
    approved_at: datetime | None = None
    promotion: dict[str, Any] | None = None

    def patch_spec(self) -> dict[str, Any]:
        return {"type": self.patch_type, "params": self.params}


class ValidationSetResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    before_pass_rate: float
    after_pass_rate: float
    cases: list[dict[str, Any]] = Field(default_factory=list)


class PatchGateResult(BaseModel):
    """The required failure, held-out, and regression proof for one patch."""

    model_config = ConfigDict(frozen=True)

    patch_id: UUID
    sets: list[ValidationSetResult]
    passed: bool
    failure_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)


class PatchService:
    """Creates and applies only bounded patches that follow a proven cause."""

    def propose_stt_keyword_boost(self, diagnosis: Diagnosis, *, keyword: str) -> PatchProposal:
        if diagnosis.verdict is not Verdict.PROVEN or diagnosis.intervention is not Intervention.STT_ORACLE:
            raise ValueError("an STT patch requires a proven STT-oracle diagnosis")
        if not keyword.strip():
            raise ValueError("a keyword boost cannot be empty")
        return PatchProposal(
            diagnosis_source_run_id=diagnosis.source_run_id,
            patch_type="stt_keyword_boost",
            stt_keyword_boosts=[keyword],
        )

    def approve(self, proposal: PatchProposal) -> PatchProposal:
        if proposal.status is not PatchStatus.PENDING:
            raise ValueError("only a pending patch can be approved")
        return proposal.model_copy(update={"status": PatchStatus.APPROVED})

    def apply(self, proposal: PatchProposal, agent: DeterministicConfirmationAgent) -> DeterministicConfirmationAgent:
        if proposal.status is not PatchStatus.APPROVED:
            raise ValueError("a person must approve a patch before it is applied")
        boosts = frozenset(agent.config["stt_keyword_boosts"]) | frozenset(proposal.stt_keyword_boosts)
        return DeterministicConfirmationAgent(stt_keyword_boosts=boosts)

    def validate(
        self,
        proposal: PatchProposal,
        *,
        base_agent: DeterministicConfirmationAgent,
        failing: list[Scenario],
        held_out: list[Scenario],
        regression: list[Scenario],
    ) -> PatchGateResult:
        patched_agent = self.apply(proposal, base_agent)
        results = [
            self._score_set("failing", failing, base_agent, patched_agent),
            self._score_set("held_out", held_out, base_agent, patched_agent),
            self._score_set("regression", regression, base_agent, patched_agent),
        ]
        failing_result, held_out_result, regression_result = results
        if failing_result.after_pass_rate <= failing_result.before_pass_rate:
            return PatchGateResult(
                patch_id=proposal.id,
                sets=results,
                passed=False,
                failure_reason="The failing scenario did not improve.",
            )
        if held_out_result.after_pass_rate <= held_out_result.before_pass_rate:
            return PatchGateResult(
                patch_id=proposal.id,
                sets=results,
                passed=False,
                failure_reason="Held-out variants did not improve.",
            )
        if regression_result.after_pass_rate < regression_result.before_pass_rate:
            return PatchGateResult(
                patch_id=proposal.id,
                sets=results,
                passed=False,
                failure_reason="The regression suite regressed.",
            )
        return PatchGateResult(patch_id=proposal.id, sets=results, passed=True)

    @staticmethod
    def _score_set(
        name: str,
        scenarios: list[Scenario],
        base_agent: DeterministicConfirmationAgent,
        patched_agent: DeterministicConfirmationAgent,
    ) -> ValidationSetResult:
        if not scenarios:
            raise ValueError(f"{name} validation set cannot be empty")

        def pass_rate(agent: DeterministicConfirmationAgent) -> float:
            return sum(
                agent.run(scenario).final_database_state == scenario.expected_database_state
                for scenario in scenarios
            ) / len(scenarios)

        return ValidationSetResult(
            name=name,
            before_pass_rate=pass_rate(base_agent),
            after_pass_rate=pass_rate(patched_agent),
        )
