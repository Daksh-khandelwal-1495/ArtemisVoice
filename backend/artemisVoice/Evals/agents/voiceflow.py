"""VoiceFlow Studio adapter (INTEGRATION_CONTRACT §1-§4).

Studio is the agent under test. Artemis never recomputes Studio's pipeline in
process: it imports a replay-grade ``voiceflow-trace/v1`` trace as a
``RunTrace(source=voiceflow)`` and delegates counterfactual replays to Studio's
sandboxed replay endpoint, which restores the recorded state and swaps exactly
one layer for its oracle.
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from artemisVoice.Evals.agents.protocol import ReplayTrial
from artemisVoice.Evals.domain.models import Scenario
from artemisVoice.Evals.tracing.contracts import (
    ArtifactRef,
    Layer,
    LayerTrace,
    RunTrace,
    TraceContractError,
    TraceSource,
    TurnTrace,
    state_hash,
)

DEFAULT_STUDIO_API_URL = "http://localhost:8000"
END_NODE_KEY = "__end_node"
STATUS_KEY = "__status"
_HEX64 = re.compile(r"^[a-f0-9]{64}$")


class StudioError(RuntimeError):
    """Studio could not be reached or answered with an error."""

    def __init__(self, message: str, *, status_code: int | None = None, detail: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class TraceNotReplayableError(TraceContractError):
    """A Studio trace that cannot honestly become a RunTrace; ``missing`` says why."""

    def __init__(self, missing: list[str]) -> None:
        super().__init__("session trace is not replay-ready: " + "; ".join(missing))
        self.missing = missing


class StudioClient:
    """Thin synchronous client for the Studio API. Inject ``http`` in tests."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        http: httpx.Client | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = (base_url or os.getenv("STUDIO_API_URL") or DEFAULT_STUDIO_API_URL).rstrip("/")
        self._http = http or httpx.Client(base_url=self.base_url, timeout=timeout)

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as error:
            raise StudioError(f"Studio is unreachable at {self.base_url}: {error}") from error
        if response.status_code >= 400:
            try:
                detail: Any = response.json()
            except ValueError:
                detail = response.text
            raise StudioError(
                f"Studio {method} {path} failed with HTTP {response.status_code}",
                status_code=response.status_code,
                detail=detail,
            )
        if not response.content:
            return None
        return response.json()

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/health")

    def get_trace(self, session_id: str) -> dict[str, Any]:
        # Unmasked: expected-state checks compare exact values (e.g. phone numbers).
        return self._request("GET", f"/api/sessions/{session_id}/trace", params={"reveal": "true"})

    def get_oracle(self, oracle_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/oracles/{oracle_id}")

    def list_oracles(self, bot_id: str) -> list[dict[str, Any]]:
        data = self._request("GET", f"/api/bots/{bot_id}/oracles")
        if isinstance(data, dict):  # tolerate {"items": [...]} envelopes
            data = data.get("items") or data.get("oracles") or []
        return list(data or [])

    def create_oracle(self, bot_id: str, oracle: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/api/bots/{bot_id}/oracles", json=oracle)

    def simulate(self, bot_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/api/bots/{bot_id}/simulate", json=body)

    def replay(self, session_id: str, body: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/api/sessions/{session_id}/replay", json=body)

    def create_patch_candidate(
        self, bot_id: str, patch: dict[str, Any], base_version_id: str | None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"patch": patch}
        if base_version_id:
            body["base_version_id"] = base_version_id
        return self._request("POST", f"/api/bots/{bot_id}/patch-candidates", json=body)

    def promote_patch_candidate(self, bot_id: str, candidate_id: str, approved_by: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/bots/{bot_id}/patch-candidates/{candidate_id}/promote",
            headers={"X-Approved-By": approved_by},
        ) or {}


# ---------------------------------------------------------------------------
# voiceflow-trace/v1 -> RunTrace


class ImportedSession(BaseModel):
    """Studio metadata kept beside an imported RunTrace (not part of the replay contract)."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    bot_id: str | None = None
    bot_slug: str | None = None
    bot_name: str | None = None
    flow_version_id: str | None = None
    flow_version: Any = None
    mode: str | None = None
    status: str | None = None
    end_node: str | None = None
    trace_source: str | None = None  # live | simulation
    config_hash: str | None = None
    seed: int | None = None
    oracle_id: str | None = None
    oracle: dict[str, Any] | None = None
    fault_profile: dict[str, Any] | None = None
    dataset_snapshot_id: str | None = None
    replay_ready: bool = False
    missing: list[str] = Field(default_factory=list)


def _as_state_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value, sort_keys=True, default=str)


def _as_state(values: dict[str, Any] | None) -> dict[str, str]:
    return {str(key): _as_state_value(value) for key, value in (values or {}).items()}


def _config_hash(value: Any) -> str:
    text = str(value or "")
    if text.startswith("sha256:"):
        text = text.removeprefix("sha256:")
    if _HEX64.match(text):
        return text
    return sha256(text.encode("utf-8")).hexdigest()


def _parse_time(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return fallback
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return fallback


def _artifact(value: Any) -> ArtifactRef | None:
    if not isinstance(value, dict):
        return None
    try:
        return ArtifactRef.model_validate(
            {"uri": value.get("uri"), "sha256": value.get("sha256"), "media_type": value.get("media_type")}
        )
    except ValidationError:
        return None


def expected_from_oracle(oracle: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Map a Studio test oracle onto RunTrace expectations (state ⊆ final, spoken contains)."""

    expected = _as_state(oracle.get("expected_fields") or {})
    if oracle.get("expected_end_node"):
        expected[END_NODE_KEY] = str(oracle["expected_end_node"])
    if oracle.get("expected_status"):
        expected[STATUS_KEY] = str(oracle["expected_status"])
    response = oracle.get("expected_bot_response") or {}
    contains = [str(item) for item in (response.get("contains") or [])] if isinstance(response, dict) else []
    return expected, contains


def session_metadata(trace: dict[str, Any], oracle: dict[str, Any] | None) -> ImportedSession:
    session = trace.get("session") or {}
    return ImportedSession(
        session_id=str(session.get("id") or trace.get("session_id") or ""),
        bot_id=_str_or_none(session.get("bot_id")),
        bot_slug=_str_or_none(session.get("bot_slug")),
        bot_name=_str_or_none(session.get("bot_name")),
        flow_version_id=_str_or_none(session.get("flow_version_id")),
        flow_version=session.get("flow_version"),
        mode=_str_or_none(session.get("mode")),
        status=_str_or_none(session.get("status")),
        end_node=_str_or_none(trace.get("end_node")),
        trace_source=_str_or_none(trace.get("source")),
        config_hash=_str_or_none(session.get("config_hash")),
        seed=trace.get("seed") if isinstance(trace.get("seed"), int) else None,
        oracle_id=_str_or_none(trace.get("oracle_id")),
        oracle=oracle,
        fault_profile=trace.get("fault_profile"),
        dataset_snapshot_id=_str_or_none(trace.get("dataset_snapshot_id")),
        replay_ready=bool(trace.get("replay_ready")),
        missing=[str(item) for item in (trace.get("missing") or [])],
    )


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def run_trace_from_studio(trace: dict[str, Any], oracle: dict[str, Any] | None) -> RunTrace:
    """Convert a replay-grade Studio trace into an Artemis RunTrace, or refuse honestly."""

    missing: list[str] = []
    if not trace.get("replay_ready"):
        missing.extend(str(item) for item in (trace.get("missing") or []))
        if not missing:
            missing.append("Studio marked the session as not replay-ready")
    if oracle is None:
        missing.append("no test oracle is bound to the session (expected outcome unknown)")
    turns_json = trace.get("turns") or []
    if not turns_json:
        missing.append("at least one recorded caller turn")
    if missing:
        raise TraceNotReplayableError(missing)
    assert oracle is not None

    meta = session_metadata(trace, oracle)
    session = trace.get("session") or {}
    now = datetime.now(UTC)
    session_start = _parse_time(session.get("started_at"), now)
    seed = trace.get("seed") if isinstance(trace.get("seed"), int) and trace["seed"] >= 0 else 0

    expected, contains = expected_from_oracle(oracle)
    final_state = _as_state(trace.get("final_state") or {})
    final_state[END_NODE_KEY] = meta.end_node or ""
    final_state[STATUS_KEY] = meta.status or ""

    previous_after = _as_state(trace.get("initial_state") or {})
    turns: list[TurnTrace] = []
    problems: list[str] = []
    for position, turn in enumerate(sorted(turns_json, key=lambda item: item.get("index", 0))):
        layers_json = turn.get("layers") or {}
        before = _as_state(turn.get("state_before")) if isinstance(turn.get("state_before"), dict) else previous_after
        after = _as_state(turn.get("state_after") or {})
        if position == len(turns_json) - 1:
            after = {**after, **final_state}
        layer_traces: list[LayerTrace] = []
        artifacts: list[ArtifactRef] = []
        cursor = session_start
        for layer in Layer:
            entry = layers_json.get(layer.value)
            if not isinstance(entry, dict):
                problems.append(f"turn {position}: {layer.value} layer not recorded")
                continue
            started = _parse_time(entry.get("started_at"), cursor)
            finished = _parse_time(entry.get("finished_at"), started)
            finished = max(finished, started)
            cursor = finished
            settings = dict(entry.get("settings") or {})
            layer_seed = settings.get("seed") if isinstance(settings.get("seed"), int) else None
            layer_traces.append(
                LayerTrace(
                    layer=layer,
                    input=dict(entry.get("input") or {}),
                    output=dict(entry.get("output") or {}),
                    settings=settings,
                    seed=layer_seed if layer_seed is None or layer_seed >= 0 else None,
                    started_at=started,
                    finished_at=finished,
                )
            )
            for where in ("input", "output"):
                ref = _artifact((entry.get(where) or {}).get("audio"))
                if ref is not None and ref not in artifacts:
                    artifacts.append(ref)
        if not artifacts:
            problems.append(f"turn {position}: caller audio artifact")
        if problems:
            continue
        turns.append(
            TurnTrace(
                index=position,
                database_state_before=before,
                database_state_after=after,
                database_hash_before=state_hash(before),
                database_hash_after=state_hash(after),
                artifacts=artifacts,
                layers=layer_traces,
            )
        )
        previous_after = after
    if problems:
        raise TraceNotReplayableError(problems)

    return RunTrace(
        scenario_id=_scenario_id(oracle, meta.session_id),
        agent_config_hash=_config_hash(meta.config_hash or session.get("config_snapshot")),
        seed=seed,
        created_at=session_start,
        valid_caller_run=True,
        expected_database_state=expected,
        expected_spoken_text="",
        final_database_state=turns[-1].database_state_after,
        turns=turns,
        source=TraceSource.VOICEFLOW,
        studio_session_id=meta.session_id,
        expected_spoken_contains=contains,
    )


def _scenario_id(oracle: dict[str, Any], session_id: str) -> str:
    raw = str(oracle.get("slug") or f"voiceflow-{session_id}").lower()
    cleaned = re.sub(r"[^a-z0-9_-]+", "-", raw).strip("-_")
    return cleaned or "voiceflow-session"


# ---------------------------------------------------------------------------
# Adapter


class VoiceFlowAdapter:
    """`VoiceAgentAdapter` + replay delegation backed by VoiceFlow Studio over HTTP."""

    def __init__(self, client: StudioClient | None = None, *, bot_id: str | None = None) -> None:
        self.client = client or StudioClient()
        self.bot_id = bot_id
        self._config_hash: str | None = None

    @property
    def config_hash(self) -> str:
        if self._config_hash:
            return self._config_hash
        return sha256(f"voiceflow:{self.client.base_url}:{self.bot_id or ''}".encode()).hexdigest()

    def import_session(self, session_id: str) -> tuple[RunTrace, ImportedSession]:
        """Fetch a Studio trace and its oracle; raise TraceNotReplayableError when not replay-grade."""

        trace = self.client.get_trace(session_id)
        oracle = None
        oracle_id = trace.get("oracle_id")
        if oracle_id:
            oracle = self.client.get_oracle(str(oracle_id))
        run = run_trace_from_studio(trace, oracle)
        self._config_hash = run.agent_config_hash
        return run, session_metadata(trace, oracle)

    def run_oracle(
        self,
        oracle_id: str,
        *,
        bot_id: str | None = None,
        seed: int | None = None,
        fault_profile: dict[str, Any] | None = None,
        mode: str = "draft",
    ) -> tuple[RunTrace, ImportedSession]:
        bot = bot_id or self.bot_id
        if not bot:
            raise ValueError("a Studio bot id is required to simulate an oracle")
        body: dict[str, Any] = {"oracle_id": oracle_id, "mode": mode}
        if seed is not None:
            body["seed"] = seed
        if fault_profile is not None:
            body["fault_profile"] = fault_profile
        result = self.client.simulate(bot, body)
        return self.import_session(str(result["session_id"]))

    def run(self, scenario: Scenario) -> RunTrace:
        """Simulate the Studio oracle whose slug (or id) equals ``scenario.id``."""

        if not self.bot_id:
            raise ValueError("VoiceFlowAdapter.run needs bot_id to find the scenario's oracle")
        oracle = next(
            (
                item
                for item in self.client.list_oracles(self.bot_id)
                if scenario.id in {str(item.get("slug")), str(item.get("id"))}
            ),
            None,
        )
        if oracle is None:
            raise ValueError(f"no Studio oracle matches scenario {scenario.id!r}")
        run, _ = self.run_oracle(str(oracle["id"]), seed=scenario.seed)
        return run

    def replay(
        self,
        source_run: RunTrace,
        turn_index: int,
        intervention: str,
        seed: int,
        *,
        trials: int = 1,
        patch: dict[str, Any] | None = None,
    ) -> list[ReplayTrial]:
        # Studio replays the whole session from its restored initial state; the
        # intervention applies to every caller turn. turn_index is evidence only.
        if source_run.source is not TraceSource.VOICEFLOW or not source_run.studio_session_id:
            raise TraceContractError("only imported VoiceFlow runs can be replayed through Studio")
        if not 1 <= trials <= 10:
            raise ValueError("Studio replays accept 1-10 trials per request")
        body: dict[str, Any] = {"intervention": intervention, "trials": trials, "seed": seed}
        if patch is not None:
            body["patch"] = patch
        response = self.client.replay(source_run.studio_session_id, body)
        results: list[ReplayTrial] = []
        for position, item in enumerate(response.get("trials") or []):
            run_trace = None
            raw = item.get("run_trace")
            if isinstance(raw, dict):
                try:
                    run_trace = RunTrace.model_validate(raw)
                except ValidationError:
                    run_trace = None
            results.append(
                ReplayTrial(
                    trial=int(item.get("trial", position)),
                    passed=bool(item.get("passed")),
                    outcome=dict(item.get("outcome") or {}),
                    run_trace=run_trace,
                    replay_session_id=_str_or_none(item.get("replay_session_id")),
                )
            )
        return results
