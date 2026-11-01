"""Bounded VoiceFlow patches derived from a proven layer (INTEGRATION_CONTRACT §4).

A proven diagnosis maps to exactly one small, reviewable PatchSpec
``{type, params}``; Studio turns it into a *draft* candidate that is validated
against failing, held-out and regression oracles before a person may approve it.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from artemisVoice.Evals.agents.voiceflow import (
    END_NODE_KEY,
    STATUS_KEY,
    ImportedSession,
    StudioClient,
)
from artemisVoice.Evals.causal.replay import Diagnosis, Intervention, Verdict
from artemisVoice.Evals.patches.service import (
    PatchGateResult,
    PatchProposal,
    ValidationSetResult,
)
from artemisVoice.Evals.tracing.contracts import (
    Layer,
    RunTrace,
    TraceSource,
    normalise_text,
)

LAYER_ALIASES: dict[str, Intervention] = {
    "stt": Intervention.STT_ORACLE,
    "speech_to_text": Intervention.STT_ORACLE,
    "stt_oracle": Intervention.STT_ORACLE,
    "endpointing": Intervention.ENDPOINTING_ORACLE,
    "endpointing_oracle": Intervention.ENDPOINTING_ORACLE,
    "llm": Intervention.LLM_ORACLE,
    "llm_oracle": Intervention.LLM_ORACLE,
    "tools": Intervention.TOOLS_ORACLE,
    "tool": Intervention.TOOLS_ORACLE,
    "tools_oracle": Intervention.TOOLS_ORACLE,
    "tts": Intervention.TTS_ORACLE,
    "text_to_speech": Intervention.TTS_ORACLE,
    "tts_oracle": Intervention.TTS_ORACLE,
}

ALLOWED_PATCH_TYPES: dict[Intervention, tuple[str, ...]] = {
    Intervention.STT_ORACLE: ("stt_keyterm_boost",),
    Intervention.ENDPOINTING_ORACLE: ("vad_stop_secs", "confirmation_readback"),
    Intervention.LLM_ORACLE: ("stricter_validation", "confirmation_readback"),
    Intervention.TOOLS_ORACLE: ("fallback_route",),
    Intervention.TTS_ORACLE: ("confirmation_readback",),
}

DEFAULT_VAD_STOP_SECS = 0.8
VAD_STOP_SECS_STEP = 0.4
HELD_OUT_TAGS = {"held_out", "held-out", "heldout"}
REGRESSION_TAGS = {"regression"}


def resolve_layer(layer: str) -> Intervention:
    try:
        return LAYER_ALIASES[layer.strip().lower()]
    except KeyError as error:
        raise ValueError(f"unknown layer {layer!r}; use stt, endpointing, llm, tools or tts") from error


def require_proven(diagnoses: list[Diagnosis], intervention: Intervention) -> Diagnosis:
    proven = [item for item in diagnoses if item.intervention is intervention and item.verdict is Verdict.PROVEN]
    if not proven:
        raise ValueError(
            f"a VoiceFlow patch for {intervention} requires a proven {intervention} diagnosis on this run"
        )
    return max(proven, key=lambda item: item.effect_size)


def _wrong_fields(run: RunTrace) -> list[str]:
    final = run.final_database_state
    return [
        key
        for key, value in run.expected_database_state.items()
        if not key.startswith("__") and normalise_text(final.get(key, "")) != normalise_text(value)
    ]


def _field_fields(run: RunTrace) -> list[str]:
    return [key for key in run.expected_database_state if key not in {END_NODE_KEY, STATUS_KEY}]


def field_pattern(value: str) -> str:
    """A conservative anchored regex describing the shape of an expected value."""

    tokens: list[tuple[str, int]] = []
    for char in value:
        if char.isdigit():
            token = r"\d"
        elif char.isalpha():
            token = "[A-Za-z]"
        elif char.isspace():
            token = r"\s"
        else:
            token = re.escape(char)
        if tokens and tokens[-1][0] == token:
            tokens[-1] = (token, tokens[-1][1] + 1)
        else:
            tokens.append((token, 1))
    body = "".join(token if count == 1 else f"{token}{{{count}}}" for token, count in tokens)
    if all(token == "[A-Za-z]" or token == r"\s" for token, _ in tokens):
        body = r"[A-Za-z][A-Za-z\s'.-]*"  # names / words: shape only, not length
    return f"^{body}$"


def _vad_stop_secs(run: RunTrace, turn_index: int) -> float:
    turn = run.turns[min(turn_index, len(run.turns) - 1)]
    endpointing = next(layer for layer in turn.layers if layer.layer is Layer.ENDPOINTING)
    vad = endpointing.settings.get("vad") or {}
    value = vad.get("stop_secs") if isinstance(vad, dict) else None
    return float(value) if isinstance(value, int | float) else DEFAULT_VAD_STOP_SECS


def _node_id(run: RunTrace, turn_index: int) -> str | None:
    turn = run.turns[min(turn_index, len(run.turns) - 1)]
    llm = next(layer for layer in turn.layers if layer.layer is Layer.LLM)
    node = llm.input.get("node_id")
    return str(node) if node else None


def build_patch_specs(
    intervention: Intervention, run: RunTrace, meta: ImportedSession, diagnosis: Diagnosis
) -> tuple[list[dict[str, Any]], str]:
    """Return candidate PatchSpecs (primary first) and a rationale for the proven layer."""

    wrong = _wrong_fields(run) or _field_fields(run)
    expected = run.expected_database_state
    turn_index = diagnosis.turn_index
    if intervention is Intervention.STT_ORACLE:
        keywords = [expected[key] for key in wrong if expected.get(key)]
        specs = [{"type": "stt_keyterm_boost", "params": {"keywords": keywords, "fields": wrong}}]
        rationale = f"STT oracle proven; boost the misheard expected values {keywords}."
    elif intervention is Intervention.ENDPOINTING_ORACLE:
        current = _vad_stop_secs(run, turn_index)
        specs = [
            {
                "type": "vad_stop_secs",
                "params": {"stop_secs": round(current + VAD_STOP_SECS_STEP, 3), "previous_stop_secs": current},
            },
            {"type": "confirmation_readback", "params": {"fields": wrong}},
        ]
        rationale = f"Endpointing oracle proven; wait {VAD_STOP_SECS_STEP}s longer before closing the turn."
    elif intervention is Intervention.LLM_ORACLE:
        field = wrong[0] if wrong else None
        params: dict[str, Any] = {"field": field, "pattern": field_pattern(expected[field]) if field else None}
        specs = [
            {"type": "stricter_validation", "params": params},
            {"type": "confirmation_readback", "params": {"fields": wrong}},
        ]
        rationale = f"LLM oracle proven; validate {field!r} strictly before accepting the tool call."
    elif intervention is Intervention.TOOLS_ORACLE:
        lookup = (meta.oracle or {}).get("expected_lookup") or {}
        specs = [
            {
                "type": "fallback_route",
                "params": {
                    "node_id": _node_id(run, turn_index),
                    "dataset": lookup.get("dataset") if isinstance(lookup, dict) else None,
                    "match_on": sorted((lookup.get("match") or {}).keys()) if isinstance(lookup, dict) else [],
                    "retries": 1,
                },
            }
        ]
        rationale = "Tools oracle proven; retry and route to a fallback when the lookup/API result is wrong."
    elif intervention is Intervention.TTS_ORACLE:
        specs = [{"type": "confirmation_readback", "params": {"fields": _field_fields(run)}}]
        rationale = "TTS oracle proven; read captured values back verbatim for confirmation."
    else:  # pragma: no cover
        raise ValueError(f"no bounded patch exists for {intervention}")
    return specs, rationale


def propose(
    *,
    run: RunTrace,
    meta: ImportedSession,
    diagnosis: Diagnosis,
    intervention: Intervention,
    studio: StudioClient,
    patch_type: str | None = None,
    report_id: UUID | None = None,
) -> PatchProposal:
    if not meta.bot_id:
        raise ValueError("the imported session has no Studio bot id")
    specs, rationale = build_patch_specs(intervention, run, meta, diagnosis)
    if patch_type is not None:
        if patch_type not in ALLOWED_PATCH_TYPES[intervention]:
            raise ValueError(
                f"{patch_type} is not a bounded patch for {intervention}; allowed: {ALLOWED_PATCH_TYPES[intervention]}"
            )
        specs = sorted(specs, key=lambda spec: spec["type"] != patch_type)
    primary, alternatives = specs[0], specs[1:]
    candidate = studio.create_patch_candidate(meta.bot_id, primary, meta.flow_version_id)
    return PatchProposal(
        diagnosis_source_run_id=run.id,
        patch_type=primary["type"],
        stt_keyword_boosts=list(primary["params"].get("keywords", [])) if primary["type"] == "stt_keyterm_boost" else [],
        source=TraceSource.VOICEFLOW,
        layer=str(diagnosis.layer or intervention),
        params=primary["params"],
        rationale=rationale,
        alternatives=alternatives,
        diagnosis_report_id=report_id,
        studio_session_id=meta.session_id,
        studio_bot_id=meta.bot_id,
        studio_base_version_id=meta.flow_version_id,
        studio_candidate_id=str(candidate.get("candidate_version_id") or candidate.get("id") or ""),
        studio_graph_diff=candidate.get("graph_diff"),
    )


def default_oracle_sets(
    studio: StudioClient, bot_id: str, source_oracle_id: str | None
) -> tuple[list[str], list[str], list[str]]:
    """Failing = the session's oracle; held-out / regression = Studio oracles tagged accordingly."""

    oracles = studio.list_oracles(bot_id)

    def tagged(tags: set[str]) -> list[str]:
        return [
            str(item["id"])
            for item in oracles
            if str(item.get("id")) != str(source_oracle_id)
            and tags & {str(tag).lower() for tag in (item.get("tags") or [])}
        ]

    failing = [str(source_oracle_id)] if source_oracle_id else []
    return failing, tagged(HELD_OUT_TAGS), tagged(REGRESSION_TAGS)


def _simulation_mode(meta: ImportedSession) -> str:
    return meta.mode if meta.mode in {"draft", "published"} else "draft"


def score_set(
    name: str,
    oracle_ids: list[str],
    *,
    studio: StudioClient,
    patch: PatchProposal,
    meta: ImportedSession,
    seed: int,
    fault_profile: dict[str, Any] | None,
) -> ValidationSetResult:
    """Simulate every oracle on the base flow and on the draft candidate."""

    assert patch.studio_bot_id
    cases: list[dict[str, Any]] = []
    for oracle_id in oracle_ids:
        base: dict[str, Any] = {"oracle_id": oracle_id, "mode": _simulation_mode(meta), "seed": seed}
        if fault_profile is not None:
            base["fault_profile"] = fault_profile
        before = studio.simulate(patch.studio_bot_id, base)
        after = studio.simulate(
            patch.studio_bot_id,
            {**base, "mode": "draft", "candidate_version_id": patch.studio_candidate_id, "patch": patch.patch_spec()},
        )
        cases.append(
            {
                "oracle_id": oracle_id,
                "before_passed": bool(before.get("passed")),
                "after_passed": bool(after.get("passed")),
                "before_session_id": _maybe_str(before.get("session_id")),
                "after_session_id": _maybe_str(after.get("session_id")),
                "after_outcome": after.get("outcome"),
            }
        )
    count = len(cases) or 1
    return ValidationSetResult(
        name=name,
        before_pass_rate=sum(case["before_passed"] for case in cases) / count,
        after_pass_rate=sum(case["after_passed"] for case in cases) / count,
        cases=cases,
    )


def _maybe_str(value: Any) -> str | None:
    return None if value is None else str(value)


def gate(patch_id: UUID, failing: ValidationSetResult, held_out: ValidationSetResult, regression: ValidationSetResult) -> PatchGateResult:
    """Pass only if the failing set improves and neither held-out nor regression drops."""

    sets = [failing, held_out, regression]
    warnings = [f"no {item.name.replace('_', '-')} oracles were supplied or tagged; that check was skipped" for item in (held_out, regression) if not item.cases]
    if not failing.cases:
        return PatchGateResult(patch_id=patch_id, sets=sets, passed=False, failure_reason="No failing oracle to validate against.", warnings=warnings)
    if failing.after_pass_rate <= failing.before_pass_rate:
        return PatchGateResult(patch_id=patch_id, sets=sets, passed=False, failure_reason="The failing case did not improve.", warnings=warnings)
    if held_out.cases and held_out.after_pass_rate < held_out.before_pass_rate:
        return PatchGateResult(patch_id=patch_id, sets=sets, passed=False, failure_reason="Held-out cases regressed.", warnings=warnings)
    if regression.cases and regression.after_pass_rate < regression.before_pass_rate:
        return PatchGateResult(patch_id=patch_id, sets=sets, passed=False, failure_reason="The regression suite regressed.", warnings=warnings)
    return PatchGateResult(patch_id=patch_id, sets=sets, passed=True, warnings=warnings)


def regression_oracle_body(meta: ImportedSession, patch: PatchProposal, name: str | None = None) -> dict[str, Any]:
    oracle = meta.oracle or {}
    slug = f"{oracle.get('slug') or 'session'}-regression-{str(patch.id)[:8]}"
    return {
        "slug": slug,
        "name": name or f"Regression: {oracle.get('name') or meta.session_id} ({patch.patch_type})",
        "caller_turns": oracle.get("caller_turns") or [],
        "expected_fields": oracle.get("expected_fields") or {},
        "expected_lookup": oracle.get("expected_lookup"),
        "expected_end_node": oracle.get("expected_end_node"),
        "expected_status": oracle.get("expected_status"),
        "expected_bot_response": oracle.get("expected_bot_response"),
        "tags": ["regression"],
    }
