from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from artemisVoice.Evals.agents.voiceflow import ImportedSession
from artemisVoice.Evals.causal.replay import CausalReport, Diagnosis
from artemisVoice.Evals.evaluation.regressions import RegressionCase
from artemisVoice.Evals.evaluation.review_queue import ReviewItem
from artemisVoice.Evals.patches.service import PatchGateResult, PatchProposal
from artemisVoice.Evals.tracing.contracts import RunTrace, TraceContractError, TurnTrace


class SQLiteTraceStore:
    """Small SQLite store; JSON preserves the complete v1 trace contract verbatim."""

    def __init__(self, path: Path | str) -> None:
        # FastAPI executes synchronous routes in a worker thread. The MVP owns one
        # local connection per app instance, so it must be usable from that thread.
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, trace_json TEXT NOT NULL)"
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS diagnoses (
                source_run_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                intervention TEXT NOT NULL,
                created_at TEXT NOT NULL,
                diagnosis_json TEXT NOT NULL,
                PRIMARY KEY (source_run_id, turn_index, intervention)
            )"""
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS patches (id TEXT PRIMARY KEY, patch_json TEXT NOT NULL)"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS patch_gate_results (patch_id TEXT PRIMARY KEY, result_json TEXT NOT NULL)"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS review_items (id TEXT PRIMARY KEY, state TEXT NOT NULL, item_json TEXT NOT NULL)"
        )
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS regression_cases (id TEXT PRIMARY KEY, case_json TEXT NOT NULL)"
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS voiceflow_imports (
                session_id TEXT PRIMARY KEY,
                run_id TEXT,
                replay_ready INTEGER NOT NULL,
                missing_json TEXT NOT NULL,
                meta_json TEXT NOT NULL,
                imported_at TEXT NOT NULL
            )"""
        )
        self._connection.execute(
            """CREATE TABLE IF NOT EXISTS diagnosis_reports (
                id TEXT PRIMARY KEY,
                source_run_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                report_json TEXT NOT NULL
            )"""
        )
        self._connection.commit()

    def ping(self) -> bool:
        return self._connection.execute("SELECT 1").fetchone() == (1,)

    def save(self, trace: RunTrace) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO runs (id, trace_json) VALUES (?, ?)",
            (str(trace.id), trace.model_dump_json()),
        )
        self._connection.commit()

    def get(self, run_id: UUID | str) -> RunTrace:
        row = self._connection.execute(
            "SELECT trace_json FROM runs WHERE id = ?", (str(run_id),)
        ).fetchone()
        if row is None:
            raise TraceContractError(f"run {run_id} was not found")
        return RunTrace.model_validate_json(row[0])

    def restore_turn(self, run_id: UUID | str, index: int) -> TurnTrace:
        return self.get(run_id).restore_turn(index)

    def save_diagnosis(self, diagnosis: Diagnosis) -> None:
        """Save the full arm evidence so a UI or report can audit the verdict."""

        self._connection.execute(
            """INSERT OR REPLACE INTO diagnoses
               (source_run_id, turn_index, intervention, created_at, diagnosis_json)
               VALUES (?, ?, ?, ?, ?)""",
            (
                str(diagnosis.source_run_id),
                diagnosis.turn_index,
                diagnosis.intervention,
                datetime.now(UTC).isoformat(),
                diagnosis.model_dump_json(),
            ),
        )
        self._connection.commit()

    def get_diagnosis(self, run_id: UUID | str, turn_index: int, intervention: str) -> Diagnosis:
        row = self._connection.execute(
            """SELECT diagnosis_json FROM diagnoses
               WHERE source_run_id = ? AND turn_index = ? AND intervention = ?""",
            (str(run_id), turn_index, intervention),
        ).fetchone()
        if row is None:
            raise TraceContractError(
                f"diagnosis for run {run_id}, turn {turn_index}, intervention {intervention} was not found"
            )
        return Diagnosis.model_validate_json(row[0])

    def list_diagnoses(self, run_id: UUID | str) -> list[Diagnosis]:
        rows = self._connection.execute(
            "SELECT diagnosis_json FROM diagnoses WHERE source_run_id = ? ORDER BY created_at, intervention",
            (str(run_id),),
        ).fetchall()
        return [Diagnosis.model_validate_json(row[0]) for row in rows]

    def save_report(self, report: CausalReport) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO diagnosis_reports (id, source_run_id, created_at, report_json) VALUES (?, ?, ?, ?)",
            (str(report.id), str(report.source_run_id), report.created_at.isoformat(), report.model_dump_json()),
        )
        self._connection.commit()

    def get_report(self, report_id: UUID | str) -> CausalReport:
        row = self._connection.execute(
            "SELECT report_json FROM diagnosis_reports WHERE id = ?", (str(report_id),)
        ).fetchone()
        if row is None:
            raise TraceContractError(f"diagnosis report {report_id} was not found")
        return CausalReport.model_validate_json(row[0])

    def latest_report(self, run_id: UUID | str) -> CausalReport:
        row = self._connection.execute(
            "SELECT report_json FROM diagnosis_reports WHERE source_run_id = ? ORDER BY created_at DESC LIMIT 1",
            (str(run_id),),
        ).fetchone()
        if row is None:
            raise TraceContractError(f"no causal report exists for run {run_id}")
        return CausalReport.model_validate_json(row[0])

    def list_reports(self, limit: int = 20) -> list[CausalReport]:
        rows = self._connection.execute(
            "SELECT report_json FROM diagnosis_reports ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [CausalReport.model_validate_json(row[0]) for row in rows]

    def save_voiceflow_import(
        self, meta: ImportedSession, *, run_id: UUID | None
    ) -> None:
        self._connection.execute(
            """INSERT OR REPLACE INTO voiceflow_imports
               (session_id, run_id, replay_ready, missing_json, meta_json, imported_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                meta.session_id,
                str(run_id) if run_id else None,
                int(run_id is not None),
                json.dumps(meta.missing),
                meta.model_dump_json(),
                datetime.now(UTC).isoformat(),
            ),
        )
        self._connection.commit()

    def get_voiceflow_import(self, session_id: str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT session_id, run_id, replay_ready, missing_json, meta_json, imported_at FROM voiceflow_imports WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return _import_row(row) if row else None

    def get_voiceflow_import_by_run(self, run_id: UUID | str) -> dict[str, Any] | None:
        row = self._connection.execute(
            "SELECT session_id, run_id, replay_ready, missing_json, meta_json, imported_at FROM voiceflow_imports WHERE run_id = ?",
            (str(run_id),),
        ).fetchone()
        return _import_row(row) if row else None

    def list_voiceflow_imports(self, *, include_refused: bool = False) -> list[dict[str, Any]]:
        query = "SELECT session_id, run_id, replay_ready, missing_json, meta_json, imported_at FROM voiceflow_imports"
        if not include_refused:
            query += " WHERE run_id IS NOT NULL"
        rows = self._connection.execute(query + " ORDER BY imported_at DESC").fetchall()
        return [_import_row(row) for row in rows]

    def list_patches(self) -> list[PatchProposal]:
        rows = self._connection.execute("SELECT patch_json FROM patches").fetchall()
        return [PatchProposal.model_validate_json(row[0]) for row in rows]

    def save_patch(self, proposal: PatchProposal) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO patches (id, patch_json) VALUES (?, ?)",
            (str(proposal.id), proposal.model_dump_json()),
        )
        self._connection.commit()

    def get_patch(self, patch_id: UUID | str) -> PatchProposal:
        row = self._connection.execute(
            "SELECT patch_json FROM patches WHERE id = ?", (str(patch_id),)
        ).fetchone()
        if row is None:
            raise TraceContractError(f"patch {patch_id} was not found")
        return PatchProposal.model_validate_json(row[0])

    def save_patch_gate_result(self, result: PatchGateResult) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO patch_gate_results (patch_id, result_json) VALUES (?, ?)",
            (str(result.patch_id), result.model_dump_json()),
        )
        self._connection.commit()

    def get_patch_gate_result(self, patch_id: UUID | str) -> PatchGateResult:
        row = self._connection.execute(
            "SELECT result_json FROM patch_gate_results WHERE patch_id = ?", (str(patch_id),)
        ).fetchone()
        if row is None:
            raise TraceContractError(f"patch gate result for {patch_id} was not found")
        return PatchGateResult.model_validate_json(row[0])

    def save_review_item(self, item: ReviewItem) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO review_items (id, state, item_json) VALUES (?, ?, ?)",
            (str(item.id), item.state, item.model_dump_json()),
        )
        self._connection.commit()

    def list_pending_review_items(self) -> list[ReviewItem]:
        rows = self._connection.execute(
            "SELECT item_json FROM review_items WHERE state = 'pending' ORDER BY id"
        ).fetchall()
        return [ReviewItem.model_validate_json(row[0]) for row in rows]

    def save_regression_case(self, case: RegressionCase) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO regression_cases (id, case_json) VALUES (?, ?)",
            (str(case.id), case.model_dump_json()),
        )
        self._connection.commit()

    def list_regression_cases(self) -> list[RegressionCase]:
        rows = self._connection.execute("SELECT case_json FROM regression_cases ORDER BY id").fetchall()
        return [RegressionCase.model_validate_json(row[0]) for row in rows]

    def close(self) -> None:
        self._connection.close()


def _import_row(row: tuple[Any, ...]) -> dict[str, Any]:
    session_id, run_id, replay_ready, missing_json, meta_json, imported_at = row
    return {
        "session_id": session_id,
        "run_id": run_id,
        "replay_ready": bool(replay_ready),
        "missing": json.loads(missing_json),
        "meta": ImportedSession.model_validate_json(meta_json),
        "imported_at": imported_at,
    }
