from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from artemisVoice.Evals.tracing.contracts import RunTrace, TraceContractError, TurnTrace


class SQLiteTraceStore:
    """Small SQLite store; JSON preserves the complete v1 trace contract verbatim."""

    def __init__(self, path: Path | str) -> None:
        self._connection = sqlite3.connect(path)
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, trace_json TEXT NOT NULL)"
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

    def close(self) -> None:
        self._connection.close()
