from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from artemisVoice.Evals.causal.replay import Diagnosis
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
        self._connection.commit()

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

    def close(self) -> None:
        self._connection.close()
