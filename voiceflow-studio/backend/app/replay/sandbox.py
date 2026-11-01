"""Replay sandbox (INTEGRATION_CONTRACT §2): dataset snapshots and side-effect-free tools.

* :func:`snapshot_datasets` freezes every dataset into ``dataset_snapshots`` (content-addressed:
  identical content is stored once). Live calls and simulations call it at session start.
* :class:`SandboxTools` serves lookups from a snapshot (never the live tables), forces
  ``API_CALL_MOCK`` semantics, refuses live HTTP (:class:`SandboxViolation`) and applies tool
  faults or the tools oracle. It records every call so the driver can trace the tools layer.
"""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Dataset, DatasetRow, DatasetSnapshot
from app.replay.faults import FaultProfile
from app.voice.compiler import filter_rows


class SandboxViolation(RuntimeError):
    """A replay/simulation attempted a live side effect (HTTP, dataset write...)."""


class SimulatedToolError(RuntimeError):
    """Injected tool failure (fault profile ``tools.api_error``)."""


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def datasets_hash(datasets: dict[str, list[dict[str, Any]]]) -> str:
    return hashlib.sha256(_canonical(datasets).encode()).hexdigest()


async def collect_datasets(db: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    """{dataset name: rows} in the order the live lookup sees them (insertion order)."""
    out: dict[str, list[dict[str, Any]]] = {}
    for ds in (await db.execute(select(Dataset).order_by(Dataset.name))).scalars().all():
        rows = (await db.execute(select(DatasetRow.data).where(DatasetRow.dataset_id == ds.id))).scalars().all()
        out[ds.name] = [dict(r) for r in rows]
    return out


async def snapshot_datasets(db: AsyncSession, *, commit: bool = True) -> uuid.UUID:
    """Freeze all datasets; returns the snapshot id (an existing identical snapshot is reused)."""
    data = await collect_datasets(db)
    digest = datasets_hash(data)
    existing = (
        await db.execute(select(DatasetSnapshot.id).where(DatasetSnapshot.content_hash == digest).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    snap = DatasetSnapshot(content_hash=digest, datasets=data)
    db.add(snap)
    await db.flush()
    if commit:
        await db.commit()
    return snap.id


async def load_snapshot(db: AsyncSession, snapshot_id: Any) -> DatasetSnapshot | None:
    try:
        sid = snapshot_id if isinstance(snapshot_id, uuid.UUID) else uuid.UUID(str(snapshot_id))
    except (ValueError, TypeError, AttributeError):
        return None
    return await db.get(DatasetSnapshot, sid)


def oracle_lookup_rows(datasets: dict[str, list[dict[str, Any]]], expected_lookup: dict[str, Any] | None,
                       limit: int = 50) -> list[dict[str, Any]]:
    """Rows of the snapshot matching the oracle's ``expected_lookup.match`` exactly."""
    if not expected_lookup or not expected_lookup.get("dataset"):
        return []
    rows = datasets.get(expected_lookup["dataset"]) or []
    match = expected_lookup.get("match") or {}
    if not match:
        return []
    return filter_rows(rows, match, "exact", limit)


class SandboxTools:
    def __init__(
        self,
        datasets: dict[str, list[dict[str, Any]]],
        *,
        profile: FaultProfile | None = None,
        tools_oracle: bool = False,
        expected_lookup: dict[str, Any] | None = None,
    ):
        self.datasets = copy.deepcopy(datasets or {})
        self.profile = profile
        self.tools_oracle = tools_oracle
        self.expected_lookup = expected_lookup
        self.calls: list[dict[str, Any]] = []
        self.results: list[dict[str, Any]] = []

    def drain(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        calls, results = self.calls, self.results
        self.calls, self.results = [], []
        return calls, results

    @property
    def _fault(self):
        return None if self.tools_oracle or self.profile is None else self.profile.tools

    async def lookup(self, dataset: str, filters: dict[str, Any], mode: str, limit: int) -> list[dict[str, Any]]:
        self.calls.append({"kind": "lookup", "dataset": dataset, "query": copy.deepcopy(filters),
                           "match_mode": mode, "limit": limit})
        source = "snapshot"
        try:
            fault = self._fault
            if self.tools_oracle and self.expected_lookup and self.expected_lookup.get("dataset") == dataset:
                rows, source = oracle_lookup_rows(self.datasets, self.expected_lookup, limit), "oracle"
            elif fault is not None and fault.api_error:
                raise SimulatedToolError(f"simulated lookup API error ({dataset})")
            elif fault is not None and fault.lookup_override and fault.lookup_override.dataset in (None, dataset):
                rows, source = copy.deepcopy(fault.lookup_override.rows)[:limit], "fault_override"
            else:
                if dataset not in self.datasets:
                    raise LookupError(f"dataset '{dataset}' not found in snapshot")
                rows = filter_rows(self.datasets[dataset], filters, mode, limit)
        except Exception as e:
            self.results.append({"kind": "lookup", "dataset": dataset, "ok": False, "error": str(e), "rows": []})
            raise
        self.results.append({"kind": "lookup", "dataset": dataset, "ok": True, "rows": copy.deepcopy(rows),
                             "source": source})
        return rows

    async def mock_api_call(self, node_id: str, method: str, url: str, body: Any, mock_result: Any) -> Any:
        self.calls.append({"kind": "api_call", "node_id": node_id, "method": method, "url": url,
                           "body": copy.deepcopy(body), "mocked": True})
        fault = self._fault
        if fault is not None and fault.api_error:
            self.results.append({"kind": "api_call", "node_id": node_id, "ok": False,
                                 "error": "simulated API error", "response": None})
            raise SimulatedToolError("simulated API error")
        self.results.append({"kind": "api_call", "node_id": node_id, "ok": True,
                             "response": copy.deepcopy(mock_result),
                             "source": "oracle" if self.tools_oracle else "mock"})
        return mock_result

    async def http_request(self, method: str, url: str, headers: dict[str, str], body: Any, timeout: float) -> Any:
        raise SandboxViolation(f"live HTTP ({method} {url}) is blocked in the replay sandbox")
