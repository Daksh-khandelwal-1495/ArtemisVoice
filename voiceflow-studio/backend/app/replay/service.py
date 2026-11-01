"""Simulate / replay orchestration (INTEGRATION_CONTRACT §2-§3).

``simulate``: scripted caller from ``oracle.caller_turns`` (deterministic, no LLM caller) through
the simulated endpointing -> STT -> real flow + LLM -> sandbox tools -> simulated TTS, recorded
as a ``mode="simulation"`` session with a contract §1 trace.

``replay``: restores the source session's ``initial_state`` + dataset snapshot and re-runs every
caller turn with one layer replaced by its oracle (or none, for ``control``); each trial is
recorded as a ``mode="replay"`` session and returned with an Artemis RunTrace.
"""

from __future__ import annotations

import copy
import os
import uuid
from datetime import date
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Bot, CallSession, FlowVersion, PatchCandidate, SessionEvent, TestOracle
from app.replay.driver import INTERVENTIONS, TextDriver, TurnInput
from app.replay.faults import FaultProfile, endpoint_segment, stt_transcribe
from app.replay.llm import LLMFactory
from app.replay.oracle import check_outcome, graph_fields, lookup_results, oracle_dict
from app.replay.patches import PatchSpec, apply_patch
from app.replay.runtrace import build_run_trace
from app.replay.sandbox import SandboxTools, load_snapshot, snapshot_datasets
from app.schemas.flow import FlowGraph
from app.schemas.providers import BotConfig


class ReplayError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(str(detail))
        self.status_code = status_code
        self.detail = detail


def default_seed() -> int:
    try:
        return int(os.environ.get("REPLAY_LLM_SEED", "7"))
    except ValueError:
        return 7


def _config_hash(config: dict[str, Any], graph: dict[str, Any]) -> str:
    from app.tracing.builder import config_hash

    return config_hash(config, graph)


def _uuid(v: Any) -> uuid.UUID | None:
    try:
        return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
    except (ValueError, TypeError, AttributeError):
        return None


# --------------------------------------------------------------------------- turn inputs


def simulated_turn(index: int, truth: str, profile: FaultProfile | None, config: BotConfig, graph: FlowGraph,
                   oracle: dict[str, Any] | None) -> TurnInput:
    """Endpointing + STT behaviour of the system under test applied to the true utterance."""
    seg, truncated = _endpoint(truth, profile, config, graph, oracle)
    heard, applied = stt_transcribe(seg, profile, config)
    return TurnInput(index=index, truth=truth, segment=seg, heard=heard, truncated=truncated, stt_applied=applied)


def _endpoint(truth: str, profile: FaultProfile | None, config: BotConfig, graph: FlowGraph,
              oracle: dict[str, Any] | None) -> tuple[str, bool]:
    field = profile.endpointing.truncate_field if profile and profile.endpointing else None
    hint = ((oracle or {}).get("expected_fields") or {}).get(field) if field else None
    ftype = graph_fields(graph).get(field).type if field and field in graph_fields(graph) else None
    return endpoint_segment(truth, profile, config, value_hint=str(hint) if hint is not None else None, field_type=ftype)


def replay_turn_inputs(
    intervention: str,
    recorded: list[dict[str, Any]],
    *,
    profile: FaultProfile | None,
    config: BotConfig,
    graph: FlowGraph,
    oracle: dict[str, Any] | None,
    recompute: bool,
) -> list[TurnInput]:
    """Per-turn bot input for an intervention (contract §3).

    ``recorded`` items: ``{index, truth, segment, heard, truncated}``. ``recompute`` (simulated
    sessions) re-derives endpointing/STT from the truth with the *current* config, so a patched
    config (keyterm boost, VAD) is honoured; live sessions use the recorded hypotheses.
    """
    out = []
    for r in recorded:
        i, truth = r["index"], r.get("truth")
        if recompute and truth is not None:
            base = simulated_turn(i, truth, profile, config, graph, oracle)
        else:
            base = TurnInput(index=i, truth=truth, segment=r.get("segment") or r.get("heard") or "",
                             heard=r.get("heard") or "", truncated=bool(r.get("truncated")), derived_from="recorded")
        if intervention == "stt_oracle" and truth is not None:
            # oracle truth of the segment that reached STT (endpointing kept)
            seg = _endpoint(truth, profile, config, graph, oracle)[0] if (recompute or profile) else truth
            base = TurnInput(index=i, truth=truth, segment=seg, heard=seg, truncated=seg != truth, derived_from="oracle")
        elif intervention == "endpointing_oracle" and truth is not None:
            # the recorded STT behaviour applied to the complete utterance: only the cut-off is fixed
            heard, applied = stt_transcribe(truth, profile, config)
            base = TurnInput(index=i, truth=truth, segment=truth, heard=heard, stt_applied=applied, derived_from="oracle")
        out.append(base)
    return out


# --------------------------------------------------------------------------- shared runner


def _synthesizer(config: BotConfig, caller_voice: str | None = None):
    from app.core.config import settings
    from app.replay.audio import make_synthesizer

    keys = {"soniox": settings.soniox_api_key, "openai": settings.openai_api_key, "deepgram": settings.deepgram_api_key}
    return make_synthesizer(config.tts, keys, caller_voice=caller_voice)


async def _run_session(
    *,
    bot: Bot,
    flow_version_id: uuid.UUID | None,
    graph: FlowGraph,
    config: BotConfig,
    llm_factory: LLMFactory,
    turns: list[TurnInput],
    datasets: dict[str, list[dict[str, Any]]],
    snapshot_id: uuid.UUID | None,
    profile: FaultProfile | None,
    oracle: dict[str, Any] | None,
    seed: int,
    mode: str,
    intervention: str,
    initial_state: dict[str, Any] | None,
    today: date | None,
    meta: dict[str, Any],
    synthesize_audio: bool = False,
    caller_voice: str | None = None,
) -> dict[str, Any]:
    from app.tracing.artifacts import ArtifactStore
    from app.tracing.collector import TraceCollector
    from app.voice.recorder import SessionRecorder

    session_id = uuid.uuid4()
    llm = llm_factory(config)
    tools = SandboxTools(datasets, profile=profile, tools_oracle=intervention == "tools_oracle",
                         expected_lookup=(oracle or {}).get("expected_lookup"))
    sensitive = {f.name for n in graph.nodes for f in n.fields if f.sensitive}
    config_json = config.model_dump(mode="json")
    recorder = SessionRecorder(
        session_id=session_id, bot_id=bot.id, flow_version_id=flow_version_id, mode=mode,
        config_snapshot=config_json, sensitive_fields=sensitive,
        mask_sensitive=config.behavior.mask_sensitive_in_logs, record_transcript=True,
    )
    source = "replay" if mode == "replay" else "simulation"
    collector = TraceCollector(session_id, recorder, ArtifactStore(), source=source)
    driver = TextDriver(
        graph=graph, config=config, llm=llm, tools=tools, seed=seed, session_id=str(session_id),
        bot_name=bot.name, recorder=recorder, collector=collector, profile=profile,
        intervention=intervention, oracle=oracle, initial_state=initial_state, today=today,
        synthesizer=_synthesizer(config, caller_voice) if synthesize_audio else None,
    )
    await recorder.start()
    collector.record_meta(
        seed=seed, initial_state=driver.initial_state,
        dataset_snapshot_id=str(snapshot_id) if snapshot_id else None,
        fault_profile=profile.dump() if profile else None,
        oracle_id=(oracle or {}).get("id"), intervention=intervention, llm_settings=driver.llm_trace_settings,
        greeting=None, **meta,
    )
    error = None
    try:
        await driver.run(turns)
    except Exception as e:  # noqa: BLE001 - record the failure honestly
        logger.exception("replay driver failed")
        error = f"{type(e).__name__}: {e}"
        recorder.event("error", driver.runtime.current_node_id, {"message": f"driver failed: {error}"[:500]})
        if driver.runtime.end_status is None:
            driver.runtime.end_status, driver.runtime.end_reason = "error", "driver_error"
    if driver.greeting_spoken:
        collector.record_meta(greeting=" ".join(driver.greeting_spoken))
    status = driver.runtime.end_status or "abandoned"
    await recorder.finish(status, driver.runtime.end_reason or "caller_script_exhausted", driver.runtime.vars)
    outcome = driver.outcome()
    if error:
        outcome["error"] = error
    run_trace = build_run_trace(
        turns=driver.turn_records, graph=graph, oracle=oracle, config_hash=_config_hash(config_json, graph.model_dump(mode="json")),
        seed=seed, studio_session_id=str(session_id), today=driver.runtime.today(),
    )
    check = check_outcome(oracle, outcome, graph, driver.runtime.today()) if oracle else None
    return {"session_id": session_id, "outcome": outcome, "check": check, "run_trace": run_trace,
            "driver": driver, "tools": tools}


async def _finish_row(db: AsyncSession, session_id: uuid.UUID, **cols: Any) -> None:
    row = await db.get(CallSession, session_id)
    if row is None:
        logger.warning(f"replay: session row {session_id} not found after recording")
        return
    for k, v in cols.items():
        setattr(row, k, v)
    await db.commit()


# --------------------------------------------------------------------------- simulate


async def resolve_flow(db: AsyncSession, bot: Bot, *, mode: str, candidate_version_id: Any = None
                       ) -> tuple[FlowVersion, dict[str, Any], PatchCandidate | None]:
    """(flow version, bot config dict, candidate) for a simulation."""
    from app.api.common import latest_version

    candidate = None
    if candidate_version_id:
        vid = _uuid(candidate_version_id)
        fv = await db.get(FlowVersion, vid) if vid else None
        if fv is None or fv.bot_id != bot.id:
            candidate = await db.get(PatchCandidate, vid) if vid else None  # tolerate a candidate id
            fv = await db.get(FlowVersion, candidate.candidate_version_id) if candidate and candidate.candidate_version_id else None
            if fv is None or fv.bot_id != bot.id:
                raise ReplayError(404, f"candidate version '{candidate_version_id}' not found for this bot")
        if candidate is None:
            candidate = (await db.execute(select(PatchCandidate).where(
                PatchCandidate.candidate_version_id == fv.id))).scalars().first()
        config = copy.deepcopy(candidate.config) if candidate is not None else copy.deepcopy(bot.config or {})
        return fv, config, candidate
    if mode == "published":
        if bot.published_version_id is None:
            raise ReplayError(409, "bot has no published version")
        fv = await db.get(FlowVersion, bot.published_version_id)
    else:
        fv = await latest_version(db, bot.id)
    if fv is None:
        raise ReplayError(404, "bot has no flow")
    return fv, copy.deepcopy(bot.config or {}), None


async def simulate(
    db: AsyncSession,
    bot: Bot,
    oracle_row: TestOracle,
    *,
    llm_factory: LLMFactory,
    fault_profile: dict[str, Any] | None = None,
    use_oracle_fault_profile: bool = True,
    mode: str = "draft",
    seed: int | None = None,
    candidate_version_id: Any = None,
    patch: dict[str, Any] | None = None,
    synthesize_audio: bool = False,
    caller_voice: str | None = None,
) -> dict[str, Any]:
    fv, config_dict, candidate = await resolve_flow(db, bot, mode=mode, candidate_version_id=candidate_version_id)
    graph_dict = copy.deepcopy(fv.graph)
    diff: list[dict[str, Any]] = []
    if patch:
        try:
            graph_dict, config_dict, diff = apply_patch(graph_dict, config_dict, PatchSpec.model_validate(patch))
        except Exception as e:  # noqa: BLE001
            raise ReplayError(422, f"invalid patch: {e}") from e
    graph = FlowGraph.model_validate(graph_dict)
    config = BotConfig.model_validate(config_dict)
    oracle = oracle_dict(oracle_row)
    fp = fault_profile if fault_profile is not None else (oracle.get("fault_profile") if use_oracle_fault_profile else None)
    try:
        profile = FaultProfile.parse(fp) if fp else None
    except Exception as e:  # noqa: BLE001
        raise ReplayError(422, f"invalid fault_profile: {e}") from e
    seed = default_seed() if seed is None else int(seed)
    snapshot_id = await snapshot_datasets(db)
    snap = await load_snapshot(db, snapshot_id)
    datasets = dict(snap.datasets or {}) if snap else {}
    turns = [simulated_turn(i, t, profile, config, graph, oracle) for i, t in enumerate(oracle["caller_turns"])]
    await db.commit()
    res = await _run_session(
        bot=bot, flow_version_id=fv.id, graph=graph, config=config, llm_factory=llm_factory, turns=turns,
        datasets=datasets, snapshot_id=snapshot_id, profile=profile, oracle=oracle, seed=seed,
        mode="simulation", intervention="control", initial_state=None, today=None,
        meta={"flow_mode": mode, "candidate_version_id": str(fv.id) if candidate_version_id else None,
              "patch": patch, "caller": "scripted", "synthesize_audio": synthesize_audio,
              "caller_voice": caller_voice},
        synthesize_audio=synthesize_audio,
        caller_voice=caller_voice,
    )
    sid = res["session_id"]
    await _finish_row(
        db, sid, seed=seed, fault_profile=profile.dump() if profile else None, oracle_id=oracle_row.id,
        turn_truths={str(i): t for i, t in enumerate(oracle["caller_turns"])}, dataset_snapshot_id=snapshot_id,
        patch_candidate_id=candidate.id if candidate else None,
    )
    return {
        "session_id": str(sid),
        "passed": bool(res["check"]["passed"]) if res["check"] else False,
        "outcome": res["outcome"],
        "checks": res["check"]["checks"] if res["check"] else None,
        "trace_url": f"/api/sessions/{sid}/trace",
        "run_trace": res["run_trace"],
        "seed": seed,
        "oracle_id": str(oracle_row.id),
        "fault_profile": profile.dump() if profile else None,
        "dataset_snapshot_id": str(snapshot_id),
        "flow_version_id": str(fv.id),
        "patch_diff": diff or None,
    }


# --------------------------------------------------------------------------- replay


async def _recorded_turns(db: AsyncSession, s: CallSession, trace: dict[str, Any] | None,
                          truths: dict[int, str]) -> list[dict[str, Any]]:
    turns = (trace or {}).get("turns") or []
    out = []
    if turns:
        for t in sorted(turns, key=lambda x: x.get("index", 0)):
            lay = t.get("layers") or {}
            stt = ((lay.get("speech_to_text") or {}).get("output") or {})
            ep = ((lay.get("endpointing") or {}).get("output") or {})
            caller = ((lay.get("caller") or {}).get("output") or {})
            i = int(t.get("index", len(out)))
            out.append({"index": i, "truth": truths.get(i, caller.get("intended_text")),
                        "heard": stt.get("final_hypothesis") or "", "segment": ep.get("segment_text"),
                        "truncated": bool(ep.get("truncated"))})
        return out
    # pre-tracing sessions: fall back to the user transcript events
    events = (await db.execute(select(SessionEvent).where(
        SessionEvent.session_id == s.id, SessionEvent.type == "user_transcript").order_by(SessionEvent.ts, SessionEvent.id))
    ).scalars().all()
    for i, e in enumerate(events):
        out.append({"index": i, "truth": truths.get(i), "heard": (e.payload or {}).get("text") or "", "segment": None})
    return out


def _truths(s: CallSession, oracle: dict[str, Any] | None) -> dict[int, str]:
    if s.turn_truths:
        out = {}
        for k, v in s.turn_truths.items():
            try:
                out[int(k)] = str(v)
            except (TypeError, ValueError):
                continue
        return out
    return {i: t for i, t in enumerate((oracle or {}).get("caller_turns") or [])}


async def replay(
    db: AsyncSession,
    session_id: Any,
    *,
    llm_factory: LLMFactory,
    intervention: str,
    trials: int = 1,
    seed: int | None = None,
    patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from app.tracing.builder import build_trace

    if intervention not in INTERVENTIONS:
        raise ReplayError(422, f"unknown intervention '{intervention}'; expected one of {list(INTERVENTIONS)}")
    sid = _uuid(session_id)
    s = await db.get(CallSession, sid) if sid else None
    if s is None:
        raise ReplayError(404, "Session not found")
    bot = await db.get(Bot, s.bot_id)
    fv = await db.get(FlowVersion, s.flow_version_id) if s.flow_version_id else None
    if fv is None or bot is None:
        raise ReplayError(409, "session has no exact flow version; cannot replay")
    oracle_row = await db.get(TestOracle, s.oracle_id) if s.oracle_id else None
    oracle = oracle_dict(oracle_row) if oracle_row else None
    trace = await build_trace(db, s.id, reveal=True)
    truths = _truths(s, oracle)
    recorded = await _recorded_turns(db, s, trace, truths)
    if not recorded:
        raise ReplayError(409, "session has no recorded caller turns to replay")

    graph_dict, config_dict = copy.deepcopy(fv.graph), copy.deepcopy(s.config_snapshot or bot.config or {})
    diff: list[dict[str, Any]] = []
    if patch:
        try:
            graph_dict, config_dict, diff = apply_patch(graph_dict, config_dict, PatchSpec.model_validate(patch))
        except Exception as e:  # noqa: BLE001
            raise ReplayError(422, f"invalid patch: {e}") from e
    graph = FlowGraph.model_validate(graph_dict)
    config = BotConfig.model_validate(config_dict)
    profile = FaultProfile.parse(s.fault_profile) if s.fault_profile else None
    seed = int(seed) if seed is not None else (s.seed if s.seed is not None else default_seed())
    initial_state = (trace or {}).get("initial_state")
    today = None
    if isinstance(initial_state, dict) and initial_state.get("today"):
        try:
            today = date.fromisoformat(str(initial_state["today"]))
        except ValueError:
            today = None

    notes: list[str] = []
    snapshot_id = s.dataset_snapshot_id
    snap = await load_snapshot(db, snapshot_id) if snapshot_id else None
    if snap is None:
        snapshot_id = await snapshot_datasets(db)
        snap = await load_snapshot(db, snapshot_id)
        notes.append("source session had no dataset snapshot; a snapshot was taken at replay time")
    datasets = dict(snap.datasets or {}) if snap else {}
    if not isinstance(initial_state, dict):
        notes.append("source session has no recorded initial_state; flow defaults were used")
        initial_state = None
    recompute = ((trace or {}).get("source") in ("simulation", "replay")) or s.mode in ("simulation", "replay")
    if patch and not recompute and patch.get("type") in ("stt_keyterm_boost", "vad_stop_secs"):
        notes.append("patch changes STT/VAD but this is a live session: recorded hypotheses are reused")
    if oracle is None:
        notes.append("no oracle bound to the session: pass/fail cannot be judged (POST /api/sessions/{id}/oracle)")
    await db.commit()

    results = []
    for trial in range(int(trials)):
        turns = replay_turn_inputs(intervention, recorded, profile=profile, config=config, graph=graph,
                                   oracle=oracle, recompute=recompute)
        res = await _run_session(
            bot=bot, flow_version_id=fv.id, graph=graph, config=config, llm_factory=llm_factory, turns=turns,
            datasets=datasets, snapshot_id=snapshot_id, profile=profile, oracle=oracle, seed=seed,
            mode="replay", intervention=intervention, initial_state=initial_state, today=today,
            meta={"replay_of": str(s.id), "trial": trial, "patch": patch},
        )
        rsid = res["session_id"]
        await _finish_row(
            db, rsid, seed=seed, fault_profile=profile.dump() if profile else None,
            oracle_id=oracle_row.id if oracle_row else None, turn_truths={str(k): v for k, v in truths.items()},
            dataset_snapshot_id=snapshot_id, replay_of_session_id=s.id, intervention=intervention,
        )
        results.append({
            "trial": trial,
            "passed": bool(res["check"]["passed"]) if res["check"] else False,
            "outcome": res["outcome"],
            "checks": res["check"]["checks"] if res["check"] else None,
            "run_trace": res["run_trace"],
            "replay_session_id": str(rsid),
        })
    return {"session_id": str(s.id), "intervention": intervention, "seed": seed, "trials": results,
            "dataset_snapshot_id": str(snapshot_id) if snapshot_id else None, "patch_diff": diff or None,
            "notes": notes}


# --------------------------------------------------------------------------- recorded outcome


async def session_outcome(db: AsyncSession, s: CallSession) -> dict[str, Any]:
    """Outcome of a recorded session (any mode) in the same shape the driver produces."""
    from app.db.models import CapturedField
    from app.tracing.builder import build_trace

    fv = await db.get(FlowVersion, s.flow_version_id) if s.flow_version_id else None
    graph = FlowGraph.model_validate(fv.graph) if fv else None
    caps = (await db.execute(select(CapturedField).where(CapturedField.session_id == s.id,
                                                         CapturedField.valid.is_(True)))).scalars().all()
    captured = {c.field_name: c.value for c in caps}
    trace = await build_trace(db, s.id, reveal=True)
    turns = (trace or {}).get("turns") or []
    final_spoken = None
    if turns:
        final_spoken = (((turns[-1].get("layers") or {}).get("text_to_speech") or {}).get("output") or {}).get("spoken_text")
    spoken = [e["payload"].get("text") for e in (trace or {}).get("events", []) if e["type"] == "bot_transcript"]
    if final_spoken is None and spoken:
        final_spoken = spoken[-1]
    return {
        "captured": captured,
        "end_node": s.current_node,
        "status": s.status,
        "end_reason": s.end_reason,
        "spoken_texts": spoken,
        "final_spoken_text": final_spoken or "",
        "lookups": lookup_results(graph, s.variables or {}) if graph else [],
        "turns": len(turns),
    }, graph
