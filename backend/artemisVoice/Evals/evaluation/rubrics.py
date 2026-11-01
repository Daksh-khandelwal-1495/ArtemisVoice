"""The cascading evaluation rubrics, scored for one run from recorded evidence only.

Order follows the cascade: caller validity → endpointing → STT → LLM/flow → tools → TTS →
end-to-end outcome → trace completeness → control replay → one-layer interventions →
causal verdict → patch scope → patch validation → regression memory → safety/isolation.

Every result cites what it was computed from. A rubric whose evidence does not exist yet
(no diagnosis run, no patch proposed) is ``pending`` — never inferred.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from artemisVoice.Evals.causal.replay import CausalReport
from artemisVoice.Evals.evaluation.regressions import RegressionCase
from artemisVoice.Evals.patches.service import PatchGateResult, PatchProposal
from artemisVoice.Evals.tracing.contracts import Layer, RunTrace, TurnTrace

Status = Literal["pass", "fail", "warn", "pending", "not_applicable"]


class RubricResult(BaseModel):
    number: int
    id: str
    name: str
    stage: str
    question: str
    status: Status
    summary: str
    details: list[str] = Field(default_factory=list)


class RubricReport(BaseModel):
    run_id: str
    counts: dict[str, int]
    rubrics: list[RubricResult]


_DIGIT_WORDS = {"zero": "0", "oh": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
                "six": "6", "seven": "7", "eight": "8", "nine": "9"}
# Layers renamed in patches ("stt") vs traces ("speech_to_text").
_PATCH_LAYER = {"stt": "speech_to_text", "tts": "text_to_speech"}


def _norm(value: Any) -> str:
    """Lowercase alphanumerics with spoken digits folded in, for value comparison."""
    words = re.findall(r"[a-z0-9]+", str(value or "").lower())
    return "".join(_DIGIT_WORDS.get(w, w) for w in words)


def _layers(turn: TurnTrace) -> dict[str, Any]:
    return {layer.layer.value: layer for layer in turn.layers}


def _expected_fields(trace: RunTrace) -> dict[str, str]:
    return {k: v for k, v in trace.expected_database_state.items() if not k.startswith("__")}


def _result(number: int, rid: str, name: str, stage: str, question: str, status: Status, summary: str,
            details: list[str] | None = None) -> RubricResult:
    return RubricResult(number=number, id=rid, name=name, stage=stage, question=question, status=status,
                        summary=summary, details=details or [])


def _caller_validity(trace: RunTrace) -> RubricResult:
    q = "Is the caller input valid, complete enough, and within the supported task scope?"
    turns = trace.turns
    intended = [_layers(t)["caller"].output.get("intended_text") for t in turns]
    known = [t for t in intended if t]
    if trace.valid_caller_run is False:
        return _result(1, "caller_validity", "Caller validity", "Per layer", q, "fail",
                       "Caller input was invalid or out of scope — the agent is not blamed for this run.")
    if not known:
        return _result(1, "caller_validity", "Caller validity", "Per layer", q, "warn",
                       "No caller ground truth recorded (live call without an oracle transcript).")
    return _result(1, "caller_validity", "Caller validity", "Per layer", q, "pass",
                   f"Valid caller run; ground-truth caller text recorded for {len(known)}/{len(turns)} turns.",
                   [f"✓ turn {i}: caller said “{t}”" for i, t in enumerate(intended) if t])


def _short(text: Any, n: int = 80) -> str:
    t = str(text or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def _endpointing(trace: RunTrace) -> RubricResult:
    q = "Was each utterance correctly bounded (no cut-off, no long wait, interruptions handled)?"
    cut, interrupted, evidence = [], [], []
    for turn in trace.turns:
        lay = _layers(turn)
        out = lay["endpointing"].output
        intended = lay["caller"].output.get("intended_text")
        segment = out.get("segment_text")
        bad = out.get("truncated") or (intended and segment is not None and _norm(segment) != _norm(intended))
        if bad:
            cut.append(turn.index)
        if out.get("interrupted_bot"):
            interrupted.append(turn.index)
        timing = f"{out.get('speech_start_ms', '?')}–{out.get('speech_end_ms', '?')} ms"
        evidence.append(f"{'✕' if bad else '✓'} turn {turn.index}: segment “{_short(segment or intended)}” · {timing}"
                        + (" · caller barged in" if out.get("interrupted_bot") else "")
                        + (f" · caller said “{_short(intended)}”" if bad else ""))
    vad = _layers(trace.turns[0])["endpointing"].settings.get("vad") or {}
    stop = vad.get("stop_secs")
    if cut:
        return _result(2, "endpointing", "Endpointing", "Per layer", q, "fail",
                       f"Utterance cut off in {len(cut)} turn(s) (VAD stop {stop}s).", evidence)
    if interrupted:
        return _result(2, "endpointing", "Endpointing", "Per layer", q, "warn",
                       f"Bounded correctly; caller barged in on turns {interrupted}.", evidence)
    return _result(2, "endpointing", "Endpointing", "Per layer", q, "pass",
                   f"All {len(trace.turns)} utterances fully bounded (VAD stop {stop}s), no interruptions.", evidence)


def _stt(trace: RunTrace) -> RubricResult:
    q = "Did speech become the correct transcript, especially high-value entities?"
    heard_all = said_all = ""
    diffs, evidence = [], []
    for turn in trace.turns:
        lay = _layers(turn)
        said = lay["endpointing"].output.get("segment_text") or lay["caller"].output.get("intended_text")
        out = lay["speech_to_text"].output
        heard = out.get("final_hypothesis") or ""
        heard_all += " " + heard
        said_all += " " + (said or "")
        ok = not said or _norm(said) == _norm(heard)
        if not ok:
            diffs.append(turn.index)
        conf = out.get("confidence")
        evidence.append(f"{'✓' if ok else '✕'} turn {turn.index}: said “{_short(said)}” → heard “{_short(heard)}”"
                        + (f" (confidence {conf:.2f})" if isinstance(conf, (int, float)) else ""))
    entity_lines, misheard = [], []
    for name, value in _expected_fields(trace).items():
        if not _norm(value) or _norm(value) not in _norm(said_all):
            continue
        hit = _norm(value) in _norm(heard_all)
        entity_lines.append(f"{'✓' if hit else '✕'} entity {name} = “{value}” {'heard' if hit else 'misheard'}")
        if not hit:
            misheard.append(name)
    evidence = entity_lines + evidence
    if misheard:
        return _result(3, "stt", "Speech-to-text", "Per layer", q, "fail",
                       f"High-value entit{'y' if len(misheard) == 1 else 'ies'} misheard: {', '.join(misheard)}.", evidence)
    if diffs:
        return _result(3, "stt", "Speech-to-text", "Per layer", q, "warn",
                       f"Entities correct; transcript differences in turns {diffs}.", evidence)
    return _result(3, "stt", "Speech-to-text", "Per layer", q, "pass",
                   f"All {len(trace.turns)} transcripts match what was said; {len(entity_lines)} entities heard correctly.",
                   evidence)


def _llm(trace: RunTrace) -> RubricResult:
    q = "Did the agent choose the right node, tool, transition and field values without inventing any?"
    expected = _expected_fields(trace)
    wrong, ungrounded, evidence = [], [], []
    for turn in trace.turns:
        lay = _layers(turn)
        heard = _norm(lay["speech_to_text"].output.get("final_hypothesis"))
        node = lay["llm"].input.get("node_id") or "?"
        for call in lay["llm"].output.get("tool_calls") or []:
            result = call.get("result") or {}
            status = result.get("status") if isinstance(result, dict) else None
            args = {k: v for k, v in (call.get("arguments") or {}).items() if v not in (None, "")}
            bad_fields = [f for f, v in args.items() if f in expected and _norm(v) != _norm(expected[f])
                          and _norm(expected[f]) in heard]
            if status == "waiting_for_caller":
                ungrounded.append(turn.index)
                mark, note = "!", "blocked: value not spoken by caller yet"
            elif bad_fields:
                wrong.extend(bad_fields)
                mark, note = "✕", f"wrong {', '.join(bad_fields)}"
            else:
                mark, note = "✓", status or "ok"
            arg_text = ", ".join(f"{k}={_short(v, 30)}" for k, v in args.items()) or "—"
            evidence.append(f"{mark} turn {turn.index} · {node}: {call.get('name')}({arg_text}) → {note}")
    end_exp = trace.expected_database_state.get("__end_node")
    end_act = trace.final_database_state.get("__end_node")
    route_ok = not end_exp or end_exp == end_act
    evidence.append(f"{'✓' if route_ok else '✕'} final node {end_act}" + ("" if route_ok else f" (expected {end_exp})"))
    if wrong or not route_ok:
        return _result(4, "llm_flow", "LLM / flow routing", "Per layer", q, "fail",
                       "Wrong field value or route chosen despite a correct transcript.", evidence)
    if ungrounded:
        return _result(4, "llm_flow", "LLM / flow routing", "Per layer", q, "warn",
                       f"Correct outcome; {len(ungrounded)} submission(s) blocked for using values the caller had not said.",
                       evidence)
    return _result(4, "llm_flow", "LLM / flow routing", "Per layer", q, "pass",
                   f"{len(evidence) - 1} tool calls with correct arguments; reached {end_act}; no invented values.", evidence)


def _tools(trace: RunTrace) -> RubricResult:
    q = "Did the right functions run with the right arguments, and did results and state match?"
    failures, evidence = 0, []
    for turn in trace.turns:
        lay = _layers(turn)["tools"]
        calls = lay.input.get("calls") or []
        for i, res in enumerate(lay.output.get("results") or []):
            res = res if isinstance(res, dict) else {}
            call = calls[i] if i < len(calls) and isinstance(calls[i], dict) else {}
            bad = res.get("ok") is False or bool(res.get("error"))
            failures += bad
            rows = res.get("rows")
            target = call.get("dataset") or call.get("url") or res.get("dataset") or ""
            query = call.get("query") or call.get("match") or {}
            outcome = res.get("error") or (f"{len(rows)} row(s)" if isinstance(rows, list) else "ok")
            evidence.append(f"{'✕' if bad else '✓'} turn {turn.index}: {res.get('kind', call.get('kind', 'tool'))} "
                            f"{target} {query if query else ''} → {outcome}".replace("  ", " "))
    expected = _expected_fields(trace)
    mismatched = 0
    for k, v in expected.items():
        actual = trace.final_database_state.get(k)
        ok = _norm(actual) == _norm(v)
        mismatched += not ok
        evidence.append(f"{'✓' if ok else '✕'} state {k} = “{actual}”" + ("" if ok else f" (expected “{v}”)"))
    if failures:
        return _result(5, "tools", "Tools / lookup / API", "Per layer", q, "fail", f"{failures} tool call(s) failed.", evidence)
    if mismatched:
        return _result(5, "tools", "Tools / lookup / API", "Per layer", q, "warn",
                       "Tools succeeded but final state differs from expected (see upstream layers).", evidence)
    return _result(5, "tools", "Tools / lookup / API", "Per layer", q, "pass",
                   f"All lookup/API results ok; final state matches all {len(expected)} expected fields.", evidence)


def _tts(trace: RunTrace) -> RubricResult:
    q = "Did the spoken response match the intended text and read back high-risk values correctly?"
    diverged, turn_lines, spoken_by_turn = [], [], []
    for turn in trace.turns:
        layer = _layers(turn)["text_to_speech"]
        source, spoken = layer.input.get("text") or "", layer.output.get("spoken_text") or ""
        spoken_by_turn.append((turn.index, _norm(spoken)))
        ok = not source or _norm(source) == _norm(spoken)
        if not ok:
            diverged.append(turn.index)
        turn_lines.append(f"{'✓' if ok else '✕'} turn {turn.index}: spoke “{_short(spoken, 110)}”"
                          + ("" if ok else f" — intended “{_short(source, 110)}”"))
    risky = {k: v for k, v in _expected_fields(trace).items() if re.search(r"\d", str(v))}
    readback_lines, unread = [], []
    for k, v in risky.items():
        turns = [i for i, text in spoken_by_turn if _norm(v) in text]
        if turns:
            readback_lines.append(f"✓ {k} “{v}” read back in turn {', '.join(map(str, turns))}")
        else:
            unread.append(k)
            readback_lines.append(f"! {k} “{v}” never read back to the caller")
    final_spoken = spoken_by_turn[-1][1] if spoken_by_turn else ""
    missing_final = [s for s in trace.expected_spoken_contains if _norm(s) not in final_spoken]
    readback_lines += [f"✕ final response missing “{s}”" for s in missing_final]
    evidence = readback_lines + turn_lines
    if diverged or missing_final:
        return _result(6, "tts", "Text-to-speech", "Per layer", q, "fail",
                       "Spoken output differs from the intended response.", evidence)
    if unread:
        return _result(6, "tts", "Text-to-speech", "Per layer", q, "warn",
                       f"Spoken text matches; high-risk values never read back: {', '.join(unread)}.", evidence)
    return _result(6, "tts", "Text-to-speech", "Per layer", q, "pass",
                   f"Spoken text matches intent every turn; read back {', '.join(risky) or 'no high-risk values'}.", evidence)


def _end_to_end(trace: RunTrace) -> RubricResult:
    q = "Did the agent reach the expected final state, terminal node/status and final spoken response?"
    exp, fin = trace.expected_database_state, trace.final_database_state
    evidence = [f"{'✓' if _norm(fin.get(k)) == _norm(v) else '✕'} {k.strip('_')}: expected “{v}”, got “{fin.get(k)}”"
                for k, v in exp.items()]
    final_spoken = _layers(trace.turns[-1])["text_to_speech"].output.get("spoken_text") or ""
    evidence += [f"{'✓' if _norm(s) in _norm(final_spoken) else '✕'} final response mentions “{s}”"
                 for s in trace.expected_spoken_contains]
    ok = trace.is_successful()
    summary = (f"Reached {fin.get('__end_node')} ({fin.get('__status')}) with every expected value."
               if ok else "Expected final state or response not reached.")
    return _result(7, "end_to_end", "End-to-end task success", "Outcome", q, "pass" if ok else "fail", summary, evidence)


def _trace_completeness(trace: RunTrace) -> RubricResult:
    q = "Were inputs, outputs, config, state snapshots, provider details and artifacts retained for replay?"
    missing, text_only, synthesized, evidence = [], 0, 0, []
    for turn in trace.turns:
        lay = _layers(turn)
        gaps = []
        if len(lay) < len(Layer):
            gaps.append(f"missing layers {sorted({x.value for x in Layer} - set(lay))}")
        for name in ("speech_to_text", "llm", "text_to_speech"):
            if name in lay and not lay[name].settings.get("model") and not lay[name].settings.get("provider"):
                gaps.append(f"{name} provider/model not recorded")
        media = []
        for name, label in (("caller", "caller"), ("text_to_speech", "bot")):
            audio = lay[name].output.get("audio") or {} if name in lay else {}
            is_audio = str(audio.get("media_type", "")).startswith("audio/")
            text_only += not is_audio
            synthesized += bool(lay[name].output.get("audio_synthesized")) if name in lay else 0
            media.append(f"{label} {'audio' if is_audio else 'text only'}")
        missing += gaps
        mark = "✕" if gaps else ("!" if "text only" in " ".join(media) else "✓")
        evidence.append(f"{mark} turn {turn.index}: {len(lay)}/6 layers, state hash {turn.database_hash_after[:8]}, "
                        + ", ".join(media) + (f" — {'; '.join(gaps)}" if gaps else ""))
    note = " Audio is synthesized (simulation), not recorded." if synthesized else ""
    if missing:
        return _result(8, "trace_completeness", "Trace completeness", "Outcome", q, "fail",
                       "Trace is missing replay-critical data.", evidence)
    if text_only:
        return _result(8, "trace_completeness", "Trace completeness", "Outcome", q, "warn",
                       f"All six layers, settings and state hashes kept; {text_only} audio artifact(s) are text only.{note}",
                       evidence)
    return _result(8, "trace_completeness", "Trace completeness", "Outcome", q, "pass",
                   f"All six layers, settings, state hashes and audio artifacts kept for {len(trace.turns)} turns.{note}",
                   evidence)


def _control_replay(trace: RunTrace, report: CausalReport | None) -> RubricResult:
    q = "With identical input, version, config, seed and sandbox, does the original outcome reproduce?"
    if report is None:
        return _result(9, "control_replay", "Control replay", "Causal", q, "pending",
                       "Not run yet — use “Run causal diagnosis”.")
    c = report.control
    original = trace.is_successful()
    reproduced = c.pass_count == (c.trials if original else 0)
    status: Status = "pass" if reproduced else ("warn" if 0 < c.pass_count < c.trials else "fail")
    return _result(9, "control_replay", "Control replay", "Causal", q, status,
                   f"Control passed {c.pass_count}/{c.trials} (original {'passed' if original else 'failed'}) — "
                   + ("baseline is stable." if reproduced else "baseline does not reproduce reliably."))


def _interventions(report: CausalReport | None) -> RubricResult:
    q = "When exactly one layer is replaced with oracle truth, does the outcome change?"
    if report is None:
        return _result(10, "interventions", "Single-layer interventions", "Causal", q, "pending",
                       "Not run yet — endpointing, STT, LLM, tools and TTS oracle arms run with the diagnosis.")
    details = [f"{a.intervention.value}: {a.intervention_arm.pass_count}/{a.intervention_arm.trials} "
               f"(effect {a.effect_size:+.2f})" for a in report.arms]
    return _result(10, "interventions", "Single-layer interventions", "Causal", q, "pass",
                   f"{len(report.arms)} oracle arms × {report.trials} trials completed.", details)


def _attribution(trace: RunTrace, report: CausalReport | None) -> RubricResult:
    q = "Control vs intervention pass rate, effect size, Fisher exact p → verdict."
    if report is None:
        if trace.is_successful():
            return _result(11, "attribution", "Causal attribution", "Causal", q, "not_applicable",
                           "Run succeeded — no failure to attribute.")
        return _result(11, "attribution", "Causal attribution", "Causal", q, "pending", "Run causal diagnosis to attribute.")
    best = max(report.arms, key=lambda a: a.effect_size)
    details = [f"{a.intervention.value}: effect {a.effect_size:+.2f}, p={a.fisher_exact_p_value:.4f}, {a.verdict.value}"
               for a in report.arms]
    status: Status = {"proven": "pass", "compound": "pass", "not_the_agent": "pass"}.get(report.verdict.value, "warn")
    return _result(11, "attribution", "Causal attribution", "Causal", q, status,
                   f"Verdict: {report.verdict.value}. {report.summary or ''} Strongest arm {best.intervention.value} "
                   f"(p={best.fisher_exact_p_value:.4f}).".strip(), details)


def _patch_scope(trace: RunTrace, report: CausalReport | None, patches: list[PatchProposal]) -> RubricResult:
    q = "Is the fix bounded to the diagnosed layer, reviewable, reversible and tied to evidence?"
    if not patches:
        status: Status = "not_applicable" if trace.is_successful() else "pending"
        return _result(12, "patch_scope", "Patch scope", "Improvement", q, status,
                       "No patch needed — run succeeded." if status == "not_applicable" else "No patch proposed yet.")
    proven = {layer.value for layer in report.proven_layers} if report else set()
    issues = []
    for p in patches:
        layer = _PATCH_LAYER.get(p.layer or "", p.layer)
        if proven and layer not in proven:
            issues.append(f"{p.patch_type} targets {layer}, proven layers are {sorted(proven)}")
        if not p.studio_candidate_id:
            issues.append(f"{p.patch_type}: no draft candidate (not reviewable/reversible)")
    return _result(12, "patch_scope", "Patch scope", "Improvement", q, "fail" if issues else "pass",
                   "Patch exceeds the diagnosed scope." if issues else
                   f"{len(patches)} patch(es) scoped to the proven layer, staged as draft candidates.",
                   issues or [f"{p.patch_type} {p.params} — {p.rationale}" for p in patches])


def _patch_validation(trace: RunTrace, patches: list[PatchProposal], gates: dict[str, PatchGateResult]) -> RubricResult:
    q = "Does the patch fix the failing case and pass held-out, healthy and regression cases?"
    if not patches:
        status: Status = "not_applicable" if trace.is_successful() else "pending"
        return _result(13, "patch_validation", "Patch validation", "Improvement", q, status,
                       "No patch to validate." if status == "not_applicable" else "Waiting for a patch.")
    gated = [gates[str(p.id)] for p in patches if str(p.id) in gates]
    if not gated:
        return _result(13, "patch_validation", "Patch validation", "Improvement", q, "pending", "Patch not validated yet.")
    details = [f"{s.name}: {s.before_pass_rate:.0%} → {s.after_pass_rate:.0%}" for g in gated for s in g.sets]
    ok = any(g.passed for g in gated)
    reason = next((g.failure_reason for g in gated if g.failure_reason), "")
    return _result(13, "patch_validation", "Patch validation", "Improvement", q, "pass" if ok else "fail",
                   "Gate passed on failing, held-out and regression sets." if ok else f"Gate failed: {reason}", details)


def _regression_memory(trace: RunTrace, patches: list[PatchProposal], regressions: list[RegressionCase]) -> RubricResult:
    q = "Is the proven failure stored as a reusable regression case?"
    ids = {p.id for p in patches}
    cases = [r for r in regressions if r.patch_id in ids or r.source_run_id == trace.id]
    if cases:
        return _result(14, "regression_memory", "Regression memory", "Improvement", q, "pass",
                       f"{len(cases)} regression case(s) saved for future suites.",
                       [f"Studio oracle {r.studio_oracle_id}" for r in cases if r.studio_oracle_id])
    status: Status = "not_applicable" if trace.is_successful() and not patches else "pending"
    return _result(14, "regression_memory", "Regression memory", "Improvement", q, status,
                   "Healthy run — nothing to remember." if status == "not_applicable" else "Not saved yet.")


def _safety(trace: RunTrace) -> RubricResult:
    q = "Are sensitive values masked and replays isolated from production effects?"
    unsafe = []
    for turn in trace.turns:
        settings = _layers(turn)["tools"].settings
        if settings.get("api_call_mock") is not True:
            unsafe.append(f"turn {turn.index}: external API calls not mocked")
        if not settings.get("sandbox"):
            unsafe.append(f"turn {turn.index}: lookups not sandboxed")
    evidence = [f"{'✓' if _layers(t)['tools'].settings.get('api_call_mock') is True and _layers(t)['tools'].settings.get('sandbox') else '✕'} "
                f"turn {t.index}: api_call_mock={_layers(t)['tools'].settings.get('api_call_mock')}, "
                f"sandbox={_layers(t)['tools'].settings.get('sandbox')}" for t in trace.turns]
    if unsafe:
        return _result(15, "safety_isolation", "Safety & replay isolation", "Governance", q, "fail",
                       "Production side effects possible.", unsafe + evidence)
    return _result(15, "safety_isolation", "Safety & replay isolation", "Governance", q, "pass",
                   "API calls mocked and lookups served from a dataset snapshot on every turn.",
                   evidence + [("! Studio masks sensitive fields in its UI and evidence export; Artemis keeps exact "
                                 "values for the expected-state check.")])


def evaluate_rubrics(
    trace: RunTrace,
    *,
    report: CausalReport | None = None,
    patches: list[PatchProposal] | None = None,
    gates: dict[str, PatchGateResult] | None = None,
    regressions: list[RegressionCase] | None = None,
) -> RubricReport:
    patches = [p for p in (patches or []) if p.diagnosis_source_run_id == trace.id]
    results = [
        _caller_validity(trace), _endpointing(trace), _stt(trace), _llm(trace), _tools(trace), _tts(trace),
        _end_to_end(trace), _trace_completeness(trace), _control_replay(trace, report), _interventions(report),
        _attribution(trace, report), _patch_scope(trace, report, patches),
        _patch_validation(trace, patches, gates or {}), _regression_memory(trace, patches, regressions or []),
        _safety(trace),
    ]
    counts = {s: sum(r.status == s for r in results) for s in ("pass", "fail", "warn", "pending", "not_applicable")}
    return RubricReport(run_id=str(trace.id), counts=counts, rubrics=results)
