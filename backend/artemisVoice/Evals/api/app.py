from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import Body, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ValidationError

from artemisVoice.Evals.agents.deterministic import DeterministicConfirmationAgent
from artemisVoice.Evals.agents.protocol import VoiceAgentAdapter
from artemisVoice.Evals.agents.voiceflow import (
    StudioClient,
    StudioError,
    TraceNotReplayableError,
    VoiceFlowAdapter,
    session_metadata,
)
from artemisVoice.Evals.causal.replay import (
    CausalReport,
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
from artemisVoice.Evals.evaluation.rubrics import RubricReport, evaluate_rubrics
from artemisVoice.Evals.patches import voiceflow as voiceflow_patches
from artemisVoice.Evals.patches.service import (
    PatchGateResult,
    PatchProposal,
    PatchService,
    PatchStatus,
)
from artemisVoice.Evals.providers.config import (
    ProviderConfigurationError,
    ProviderSettings,
)
from artemisVoice.Evals.storage.sqlite import SQLiteTraceStore
from artemisVoice.Evals.tracing.contracts import (
    RunTrace,
    TraceContractError,
    TraceSource,
)

DEFAULT_CORS_ORIGINS = "http://localhost:5173,http://localhost:3000"
OPTIONAL_JSON_BODY = Body(default=None)


class DiagnosisRequest(BaseModel):
    turn_index: int | None = Field(default=None, ge=0)
    trials: int = Field(default=5, ge=1)
    intervention: Intervention = Intervention.STT_ORACLE


class DiagnoseAllRequest(BaseModel):
    trials: int = Field(default=5, ge=1, le=10)
    turn_index: int | None = Field(default=None, ge=0)


class VoiceFlowImportRequest(BaseModel):
    session_id: str = Field(min_length=1)


class VoiceFlowPatchRequest(BaseModel):
    source_run_id: UUID
    layer: str = Field(min_length=1)
    patch_type: str | None = None


class VoiceFlowValidationRequest(BaseModel):
    failing_oracle_ids: list[str] | None = None
    held_out_oracle_ids: list[str] | None = None
    regression_oracle_ids: list[str] | None = None
    seed: int | None = Field(default=None, ge=0)
    fault_profile: dict[str, Any] | None = None
    use_source_fault_profile: bool = True


class ApprovalRequest(BaseModel):
    approved_by: str | None = None


class RememberRequest(BaseModel):
    name: str | None = None


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
    studio: StudioClient | None = None,
) -> FastAPI:
    """Create a local-first API instance; no vendor credentials are required for the MVP.

    ``studio`` is the VoiceFlow Studio client (defaults to ``STUDIO_API_URL``);
    tests inject one backed by a fake Studio.
    """

    app = FastAPI(title="ArtemisVoice Evals", version="0.2.0")
    origins = [
        origin.strip()
        for origin in os.getenv("ARTEMIS_CORS_ORIGINS", DEFAULT_CORS_ORIGINS).split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    store = SQLiteTraceStore(database_path)
    agent_under_test = agent or DeterministicConfirmationAgent()
    studio_client = studio or StudioClient()
    voiceflow = VoiceFlowAdapter(studio_client)
    engine = CounterfactualReplayEngine(adapter=voiceflow)
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

    @app.get("/runs/{run_id}/rubrics", response_model=RubricReport)
    def read_rubrics(run_id: UUID) -> RubricReport:
        """The 15 cascading eval rubrics for one run, scored from its stored evidence."""
        trace = get_run(run_id)
        try:
            report = store.latest_report(run_id)
        except TraceContractError:
            report = None
        patches = [p for p in store.list_patches() if p.diagnosis_source_run_id == trace.id]
        gates = {}
        for patch in patches:
            try:
                gates[str(patch.id)] = store.get_patch_gate_result(patch.id)
            except TraceContractError:
                pass
        return evaluate_rubrics(trace, report=report, patches=patches, gates=gates,
                                regressions=store.list_regression_cases())

    def run_diagnosis(run_id: UUID, request: DiagnosisRequest, intervention: Intervention) -> Diagnosis:
        trace = get_run(run_id)
        try:
            if trace.source is not TraceSource.DETERMINISTIC and request.trials > 10:
                raise ValueError("Studio replays accept 1-10 trials")
            turn_index = (
                request.turn_index if request.turn_index is not None else engine.default_turn_index(trace)
            )
            diagnosis = engine.diagnose(
                trace, turn_index=turn_index, intervention=intervention, trials=request.trials
            )
            store.save_diagnosis(diagnosis)
            return diagnosis
        except StudioError as error:
            raise studio_http_error(error) from error
        except (TraceContractError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.post("/runs/{run_id}/diagnoses/stt", response_model=Diagnosis, status_code=201)
    def diagnose_stt(run_id: UUID, request: DiagnosisRequest) -> Diagnosis:
        return run_diagnosis(run_id, request, Intervention.STT_ORACLE)

    @app.post("/runs/{run_id}/diagnoses", response_model=Diagnosis, status_code=201)
    def diagnose_layer(run_id: UUID, request: DiagnosisRequest) -> Diagnosis:
        return run_diagnosis(run_id, request, request.intervention)

    @app.get("/runs/{run_id}/diagnoses", response_model=list[Diagnosis])
    def list_run_diagnoses(run_id: UUID) -> list[Diagnosis]:
        get_run(run_id)
        return store.list_diagnoses(run_id)

    @app.post("/runs/{run_id}/diagnoses/all", response_model=CausalReport, status_code=201)
    def diagnose_all(run_id: UUID, request: DiagnoseAllRequest | None = None) -> CausalReport:
        request = request or DiagnoseAllRequest()
        trace = get_run(run_id)
        try:
            report = engine.diagnose_all(trace, turn_index=request.turn_index, trials=request.trials)
        except StudioError as error:
            raise studio_http_error(error) from error
        except (TraceContractError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        for arm in report.arms:
            store.save_diagnosis(arm)
        store.save_report(report)
        return report

    @app.get("/runs/{run_id}/diagnoses/all", response_model=CausalReport)
    def latest_report(run_id: UUID) -> CausalReport:
        try:
            return store.latest_report(run_id)
        except TraceContractError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    @app.get("/diagnoses", response_model=list[CausalReport])
    def recent_reports(limit: int = Query(default=20, ge=1, le=200)) -> list[CausalReport]:
        return store.list_reports(limit)

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

    def get_patch(patch_id: UUID) -> PatchProposal:
        try:
            return store.get_patch(patch_id)
        except TraceContractError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

    def get_gate(patch_id: UUID) -> PatchGateResult | None:
        try:
            return store.get_patch_gate_result(patch_id)
        except TraceContractError:
            return None

    @app.get("/patches")
    def list_patches() -> list[dict[str, Any]]:
        return [
            {"patch": patch.model_dump(mode="json"), "gate": _dump(get_gate(patch.id))}
            for patch in store.list_patches()
        ]

    @app.get("/patches/{patch_id}")
    def read_patch(patch_id: UUID) -> dict[str, Any]:
        patch = get_patch(patch_id)
        return {"patch": patch.model_dump(mode="json"), "gate": _dump(get_gate(patch_id))}

    @app.post("/patches/{patch_id}/approve", response_model=PatchProposal)
    def approve_patch(
        patch_id: UUID,
        request: ApprovalRequest | None = None,
        x_approved_by: str | None = Header(default=None),
    ) -> PatchProposal:
        patch = get_patch(patch_id)
        if patch.source is TraceSource.VOICEFLOW:
            return approve_voiceflow_patch(patch, (request.approved_by if request else None) or x_approved_by)
        try:
            patch = patch_service.approve(patch)
            store.save_patch(patch)
            return patch
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    def approve_voiceflow_patch(patch: PatchProposal, approver: str | None) -> PatchProposal:
        if not approver or not approver.strip():
            raise HTTPException(status_code=422, detail="approval requires a named human approver (approved_by or X-Approved-By)")
        if patch.status is not PatchStatus.PENDING:
            raise HTTPException(status_code=409, detail="only a pending patch can be approved")
        gate = get_gate(patch.id)
        if gate is None:
            raise HTTPException(status_code=409, detail="validate the patch before approving it")
        if not gate.passed:
            raise HTTPException(status_code=409, detail=f"the patch failed its validation gate: {gate.failure_reason}")
        try:
            promotion = studio_client.promote_patch_candidate(
                str(patch.studio_bot_id), str(patch.studio_candidate_id), approver.strip()
            )
        except StudioError as error:
            raise studio_http_error(error) from error
        approved = patch.model_copy(
            update={
                "status": PatchStatus.APPROVED,
                "approved_by": approver.strip(),
                "approved_at": datetime.now(UTC),
                "promotion": promotion,
            }
        )
        store.save_patch(approved)
        return approved

    @app.post("/patches/{patch_id}/validate", response_model=PatchGateResult, status_code=201)
    def validate_patch(patch_id: UUID, body: dict[str, Any] | None = OPTIONAL_JSON_BODY) -> PatchGateResult:
        patch = get_patch(patch_id)
        if patch.source is TraceSource.VOICEFLOW:
            try:
                request = VoiceFlowValidationRequest.model_validate(body or {})
            except ValidationError as error:
                raise HTTPException(status_code=422, detail=error.errors(include_url=False)) from error
            return validate_voiceflow_patch(patch, request)
        try:
            request = PatchValidationRequest.model_validate(body or {})
        except ValidationError as error:
            raise HTTPException(status_code=422, detail=error.errors(include_url=False)) from error
        try:
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
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    def validate_voiceflow_patch(patch: PatchProposal, request: VoiceFlowValidationRequest) -> PatchGateResult:
        if patch.status is not PatchStatus.PENDING:
            raise HTTPException(status_code=409, detail="only a pending patch can be validated")
        run = get_run(patch.diagnosis_source_run_id)
        meta = import_meta(run.id)
        try:
            failing, held_out, regression = voiceflow_patches.default_oracle_sets(
                studio_client, str(patch.studio_bot_id), meta.oracle_id
            ) if None in (request.failing_oracle_ids, request.held_out_oracle_ids, request.regression_oracle_ids) else ([], [], [])
            failing = request.failing_oracle_ids if request.failing_oracle_ids is not None else failing
            held_out = request.held_out_oracle_ids if request.held_out_oracle_ids is not None else held_out
            regression = request.regression_oracle_ids if request.regression_oracle_ids is not None else regression
            fault_profile = request.fault_profile
            if fault_profile is None and request.use_source_fault_profile:
                fault_profile = meta.fault_profile
            seed = request.seed if request.seed is not None else run.seed
            scored = [
                voiceflow_patches.score_set(
                    name, ids, studio=studio_client, patch=patch, meta=meta, seed=seed, fault_profile=fault_profile
                )
                for name, ids in (("failing", failing), ("held_out", held_out), ("regression", regression))
            ]
        except StudioError as error:
            raise studio_http_error(error) from error
        result = voiceflow_patches.gate(patch.id, *scored)
        store.save_patch_gate_result(result)
        return result

    @app.post("/patches/{patch_id}/remember", response_model=RegressionCase, status_code=201)
    def remember_regression(patch_id: UUID, body: dict[str, Any] | None = OPTIONAL_JSON_BODY) -> RegressionCase:
        patch = get_patch(patch_id)
        gate = get_gate(patch_id)
        if gate is None:
            raise HTTPException(status_code=404, detail=f"patch gate result for {patch_id} was not found")
        if patch.source is TraceSource.VOICEFLOW:
            return remember_voiceflow(patch, gate, RememberRequest.model_validate(body or {}))
        try:
            scenario = Scenario.model_validate(body or {})
        except ValidationError as error:
            raise HTTPException(status_code=422, detail=error.errors(include_url=False)) from error
        try:
            regression = regression_service.remember(patch, gate, scenario)
            store.save_regression_case(regression)
            return regression
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    def remember_voiceflow(patch: PatchProposal, gate: PatchGateResult, request: RememberRequest) -> RegressionCase:
        if not gate.passed:
            raise HTTPException(status_code=422, detail="only a patch that passed its validation gate can create a regression case")
        if patch.status is not PatchStatus.APPROVED:
            raise HTTPException(status_code=409, detail="a person must approve the patch before its failure is remembered")
        meta = import_meta(patch.diagnosis_source_run_id)
        try:
            oracle = studio_client.create_oracle(
                str(patch.studio_bot_id), voiceflow_patches.regression_oracle_body(meta, patch, request.name)
            )
        except StudioError as error:
            raise studio_http_error(error) from error
        regression = regression_service.remember_voiceflow(
            patch, gate, source_run_id=patch.diagnosis_source_run_id, studio_oracle=oracle
        )
        store.save_regression_case(regression)
        return regression

    @app.get("/regressions", response_model=list[RegressionCase])
    def list_regressions() -> list[RegressionCase]:
        return store.list_regression_cases()

    # ------------------------------------------------------------------ VoiceFlow

    def import_meta(run_id: UUID):
        row = store.get_voiceflow_import_by_run(run_id)
        if row is None:
            raise HTTPException(status_code=422, detail=f"run {run_id} is not an imported VoiceFlow run")
        return row["meta"]

    def run_summary(row: dict[str, Any]) -> dict[str, Any]:
        meta = row["meta"]
        summary: dict[str, Any] = {
            "run_id": row["run_id"],
            "session_id": row["session_id"],
            "bot_id": meta.bot_id,
            "bot_name": meta.bot_name,
            "flow_version_id": meta.flow_version_id,
            "flow_version": meta.flow_version,
            "mode": meta.mode,
            "status": meta.status,
            "end_node": meta.end_node,
            "trace_source": meta.trace_source,
            "oracle_id": meta.oracle_id,
            "oracle_slug": (meta.oracle or {}).get("slug"),
            "fault_profile": meta.fault_profile,
            "replay_ready": row["replay_ready"],
            "missing": row["missing"],
            "imported_at": row["imported_at"],
            "studio_session_url": f"{studio_ui_url()}/sessions/{row['session_id']}",
            "scenario_id": None,
            "turn_count": None,
            "passed": None,
        }
        if row["run_id"]:
            try:
                trace = store.get(row["run_id"])
            except TraceContractError:
                return summary
            summary.update(
                scenario_id=trace.scenario_id, turn_count=len(trace.turns), passed=trace.is_successful()
            )
        return summary

    @app.post("/voiceflow/import", status_code=201)
    def import_voiceflow(request: VoiceFlowImportRequest) -> dict[str, Any]:
        existing = store.get_voiceflow_import(request.session_id)
        if existing and existing["run_id"]:
            return {**run_summary(existing), "created": False}
        try:
            trace, meta = voiceflow.import_session(request.session_id)
        except TraceNotReplayableError as error:
            try:
                raw = studio_client.get_trace(request.session_id)
                store.save_voiceflow_import(
                    session_metadata(raw, None).model_copy(update={"replay_ready": False, "missing": error.missing}),
                    run_id=None,
                )
            except StudioError:
                pass
            raise HTTPException(
                status_code=422,
                detail={"message": "session is not replay-ready", "session_id": request.session_id, "missing": error.missing},
            ) from error
        except StudioError as error:
            raise studio_http_error(error) from error
        store.save(trace)
        store.save_voiceflow_import(meta.model_copy(update={"replay_ready": True, "missing": []}), run_id=trace.id)
        row = store.get_voiceflow_import(request.session_id)
        assert row is not None
        return {**run_summary(row), "created": True}

    @app.get("/voiceflow/runs")
    def list_voiceflow_runs(include_refused: bool = False) -> list[dict[str, Any]]:
        return [run_summary(row) for row in store.list_voiceflow_imports(include_refused=include_refused)]

    @app.get("/voiceflow/runs/{run_id}")
    def read_voiceflow_run(run_id: UUID) -> dict[str, Any]:
        row = store.get_voiceflow_import_by_run(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"VoiceFlow run {run_id} was not found")
        return {**run_summary(row), "oracle": row["meta"].oracle, "trace": get_run(run_id).model_dump(mode="json")}

    @app.post("/voiceflow/patches", response_model=PatchProposal, status_code=201)
    def propose_voiceflow_patch(request: VoiceFlowPatchRequest) -> PatchProposal:
        run = get_run(request.source_run_id)
        if run.source is not TraceSource.VOICEFLOW:
            raise HTTPException(status_code=422, detail="VoiceFlow patches require an imported VoiceFlow run")
        meta = import_meta(run.id)
        try:
            intervention = voiceflow_patches.resolve_layer(request.layer)
            diagnosis = voiceflow_patches.require_proven(store.list_diagnoses(run.id), intervention)
            report_id = None
            try:
                report_id = store.latest_report(run.id).id
            except TraceContractError:
                pass
            patch = voiceflow_patches.propose(
                run=run,
                meta=meta,
                diagnosis=diagnosis,
                intervention=intervention,
                studio=studio_client,
                patch_type=request.patch_type,
                report_id=report_id,
            )
        except StudioError as error:
            raise studio_http_error(error) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        store.save_patch(patch)
        return patch

    # ------------------------------------------------------------------ Readiness

    @app.get("/readiness")
    def readiness() -> dict[str, Any]:
        try:
            database_ok = store.ping()
            database_error = None
        except Exception as error:  # noqa: BLE001 - readiness must report, not raise
            database_ok, database_error = False, str(error)
        try:
            settings = ProviderSettings.from_environment()
            settings.validate()
            artemis_providers: dict[str, Any] = {
                "mode": settings.mode,
                "stt": settings.stt,
                "tts": settings.tts,
                "judge": settings.judge,
                "research": settings.research,
                "openai_configured": settings.openai_configured,
                "cerebras_configured": settings.cerebras_configured,
                "exa_configured": settings.exa_configured,
                "valid": True,
                "error": None,
            }
        except ProviderConfigurationError as error:
            artemis_providers = {"valid": False, "error": str(error)}
        studio_status: dict[str, Any] = {"url": studio_client.base_url, "ui_url": studio_ui_url(), "reachable": False}
        studio_providers = None
        try:
            health = studio_client.health() or {}
            studio_status.update(reachable=True, ok=bool(health.get("ok")), database_ok=health.get("db"), error=None)
            studio_providers = health.get("providers")
        except StudioError as error:
            studio_status.update(ok=False, database_ok=None, error=str(error))
        rows = store.list_voiceflow_imports(include_refused=True) if database_ok else []
        imported = [row for row in rows if row["run_id"]]
        refused = [row for row in rows if not row["run_id"]]
        return {
            "ok": database_ok and studio_status["reachable"] and bool(artemis_providers.get("valid")),
            "database": {"ok": database_ok, "error": database_error},
            "studio": studio_status,
            "providers": {"artemis": artemis_providers, "studio": studio_providers},
            "replay": {
                "imported_runs": len(imported),
                "replay_ready": sum(row["replay_ready"] for row in imported),
                "refused_imports": len(refused),
            },
            "runs": [
                {
                    "run_id": row["run_id"],
                    "session_id": row["session_id"],
                    "replay_ready": row["replay_ready"],
                    "missing": row["missing"],
                }
                for row in rows
            ],
        }

    return app


def studio_ui_url() -> str:
    return os.getenv("STUDIO_UI_URL", "http://localhost:3000").rstrip("/")


def studio_http_error(error: StudioError) -> HTTPException:
    status = 404 if error.status_code == 404 else 502
    return HTTPException(
        status_code=status,
        detail={"message": str(error), "studio_status": error.status_code, "studio_detail": error.detail},
    )


def _dump(model: BaseModel | None) -> dict[str, Any] | None:
    return None if model is None else model.model_dump(mode="json")


app = create_app(os.getenv("ARTEMIS_DATABASE_PATH", "artemis_voice.db"))
