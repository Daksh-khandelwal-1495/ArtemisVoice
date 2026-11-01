from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from artemisVoice.Evals.agents.deterministic import DeterministicConfirmationAgent
from artemisVoice.Evals.agents.protocol import VoiceAgentAdapter
from artemisVoice.Evals.causal.replay import (
    CounterfactualReplayEngine,
    Diagnosis,
    Intervention,
)
from artemisVoice.Evals.domain.models import Scenario
from artemisVoice.Evals.evaluation.judges import (
    JudgeEvidenceError,
    JudgeResult,
    deterministic_judges,
    needs_human_review,
    validate_evidence,
)
from artemisVoice.Evals.evaluation.regressions import RegressionCase, RegressionService
from artemisVoice.Evals.evaluation.review_queue import ReviewItem
from artemisVoice.Evals.patches.service import (
    PatchGateResult,
    PatchProposal,
    PatchService,
)
from artemisVoice.Evals.storage.sqlite import SQLiteTraceStore
from artemisVoice.Evals.tracing.contracts import RunTrace, TraceContractError


class DiagnosisRequest(BaseModel):
    turn_index: int = Field(ge=0)
    trials: int = Field(default=5, ge=1)
    intervention: Intervention = Intervention.STT_ORACLE


class PatchRequest(BaseModel):
    source_run_id: UUID
    turn_index: int = Field(ge=0)
    keyword: str = Field(min_length=1)


class PatchValidationRequest(BaseModel):
    failing: list[Scenario] = Field(min_length=1)
    held_out: list[Scenario] = Field(min_length=1)
    regression: list[Scenario] = Field(min_length=1)


class JudgeReviewRequest(BaseModel):
    results: list[JudgeResult] = Field(min_length=1)


def create_app(
    database_path: Path | str = "artemis_voice.db",
    *,
    agent: VoiceAgentAdapter | None = None,
) -> FastAPI:
    """Create a local-first API instance; no vendor credentials are required for the MVP."""

    app = FastAPI(title="ArtemisVoice Evals", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    store = SQLiteTraceStore(database_path)
    agent_under_test = agent or DeterministicConfirmationAgent()
    engine = CounterfactualReplayEngine()
    patch_service = PatchService()
    regression_service = RegressionService()

    def get_run(run_id: UUID) -> RunTrace:
        try:
            return store.get(run_id)
        except TraceContractError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/runs", response_model=RunTrace, status_code=201)
    def create_run(scenario: Scenario) -> RunTrace:
        trace = agent_under_test.run(scenario)
        store.save(trace)
        return trace

    @app.get("/runs/{run_id}", response_model=RunTrace)
    def read_run(run_id: UUID) -> RunTrace:
        return get_run(run_id)

    @app.post("/runs/{run_id}/diagnoses/stt", response_model=Diagnosis, status_code=201)
    def diagnose_stt(run_id: UUID, request: DiagnosisRequest) -> Diagnosis:
        try:
            diagnosis = engine.diagnose(
                get_run(run_id),
                turn_index=request.turn_index,
                intervention=Intervention.STT_ORACLE,
                trials=request.trials,
            )
            store.save_diagnosis(diagnosis)
            return diagnosis
        except (TraceContractError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/runs/{run_id}/diagnoses", response_model=Diagnosis, status_code=201)
    def diagnose_layer(run_id: UUID, request: DiagnosisRequest) -> Diagnosis:
        try:
            diagnosis = engine.diagnose(
                get_run(run_id),
                turn_index=request.turn_index,
                intervention=request.intervention,
                trials=request.trials,
            )
            store.save_diagnosis(diagnosis)
            return diagnosis
        except (TraceContractError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/runs/{run_id}/diagnoses/stt/{turn_index}", response_model=Diagnosis)
    def read_stt_diagnosis(run_id: UUID, turn_index: int) -> Diagnosis:
        try:
            return store.get_diagnosis(run_id, turn_index, "stt_oracle")
        except TraceContractError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/runs/{run_id}/judges", response_model=list[JudgeResult])
    def judge_run(run_id: UUID) -> list[JudgeResult]:
        return deterministic_judges(get_run(run_id))

    @app.post("/runs/{run_id}/reviews", response_model=ReviewItem, status_code=201)
    def queue_judge_review(run_id: UUID, request: JudgeReviewRequest) -> ReviewItem:
        trace = get_run(run_id)
        try:
            results = [validate_evidence(trace, result) for result in request.results]
        except JudgeEvidenceError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if not needs_human_review(results):
            raise HTTPException(status_code=422, detail="The supplied judge results do not require review.")
        item = ReviewItem(run_id=run_id, reason="judge abstention, low confidence, or safety flag", judge_results=results)
        store.save_review_item(item)
        return item

    @app.get("/reviews", response_model=list[ReviewItem])
    def list_reviews() -> list[ReviewItem]:
        return store.list_pending_review_items()

    @app.post("/patches/stt-keyword-boost", response_model=PatchProposal, status_code=201)
    def create_stt_patch(request: PatchRequest) -> PatchProposal:
        try:
            diagnosis = store.get_diagnosis(request.source_run_id, request.turn_index, "stt_oracle")
            patch = patch_service.propose_stt_keyword_boost(diagnosis, keyword=request.keyword)
            store.save_patch(patch)
            return patch
        except TraceContractError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/patches/{patch_id}/approve", response_model=PatchProposal)
    def approve_patch(patch_id: UUID) -> PatchProposal:
        try:
            patch = patch_service.approve(store.get_patch(patch_id))
            store.save_patch(patch)
            return patch
        except TraceContractError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/patches/{patch_id}/validate", response_model=PatchGateResult, status_code=201)
    def validate_patch(patch_id: UUID, request: PatchValidationRequest) -> PatchGateResult:
        try:
            patch = store.get_patch(patch_id)
            if not isinstance(agent_under_test, DeterministicConfirmationAgent):
                raise HTTPException(
                    status_code=501,
                    detail="Patch application needs an adapter-specific patch handler.",
                )
            result = patch_service.validate(
                patch,
                base_agent=agent_under_test,
                failing=request.failing,
                held_out=request.held_out,
                regression=request.regression,
            )
            store.save_patch_gate_result(result)
            return result
        except TraceContractError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/patches/{patch_id}/remember", response_model=RegressionCase, status_code=201)
    def remember_regression(patch_id: UUID, scenario: Scenario) -> RegressionCase:
        try:
            regression = regression_service.remember(
                store.get_patch(patch_id), store.get_patch_gate_result(patch_id), scenario
            )
            store.save_regression_case(regression)
            return regression
        except TraceContractError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.get("/regressions", response_model=list[RegressionCase])
    def list_regressions() -> list[RegressionCase]:
        return store.list_regression_cases()

    return app


app = create_app()
