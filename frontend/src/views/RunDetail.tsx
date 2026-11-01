import { ReactElement, ReactNode, useEffect, useState } from "react";
import { getRubrics, Rubric, RubricStatus, studioApiUrl,
  ApiError, diagnoseAll, Diagnosis, DiagnosisSet, getRun, interventionLabel, interventionLayer, interventionPatchLayer,
  latestReport, Layer, layerMeta, LAYERS, proposeVoiceflowPatch, provenDiagnosis, Run, studioSessionUrl, Turn, verdictSentence,
} from "../api";
import { ArtifactPlayer, Badge, display, Empty, ErrorBox, Json, KeyValues, Loading, PageHeader, Panel, PassBar, relativeTime, shortId, useAsync } from "../components/ui";
import { href, navigate } from "../router";
import { latestDiagnosisFor, recordDiagnosis, recordPatch } from "../store";
import { asArtifact, changedKeys, lastSpokenText, layersOf, num, obj, runOutcome, stateAfter, stateBefore, stateDiff, text, tokenDiff } from "../trace";

type Json = Record<string, unknown>;

function durationMs(layer: Layer | undefined): number | null {
  if (!layer?.started_at || !layer.finished_at) return null;
  const ms = new Date(layer.finished_at).getTime() - new Date(layer.started_at).getTime();
  return Number.isFinite(ms) && ms >= 0 ? ms : null;
}

function Diffed({ reference, actual }: { reference: string | null; actual: string | null }) {
  if (!actual) return <span className="muted">—</span>;
  return <span className="quote">{tokenDiff(reference, actual).map((part, index) => <span key={index} className={part.bad ? "token-bad" : undefined}>{part.token}</span>)}</span>;
}

function Confidence({ value }: { value: number | null }) {
  if (value === null) return <span className="muted">—</span>;
  const pct = Math.max(0, Math.min(1, value));
  return <span className="conf"><span className="conf-track"><span className={`conf-fill ${pct < 0.8 ? "warn" : "good"}`} style={{ width: `${pct * 100}%` }} /></span><span className="mono">{pct.toFixed(2)}</span></span>;
}

function Settings({ settings }: { settings?: Json }) {
  const entries = Object.entries(settings ?? {}).filter(([, value]) => value !== null && value !== undefined && typeof value !== "object");
  if (!entries.length) return null;
  return <div className="chips">{entries.slice(0, 5).map(([key, value]) => <span key={key} className="chip" title={`${key}: ${String(value)}`}>{typeof value === "string" && Number.isNaN(Number(value)) ? value : `${key.replaceAll("_", " ")} ${String(value)}`}</span>)}</div>;
}

type CellProps = { layer: Layer | undefined; turn: Turn; layers: Record<string, Layer>; intended: string | null; sessionId?: string | null };

function CallerCell({ layer, turn, intended }: CellProps) {
  const audio = asArtifact(layer?.output?.audio) ?? turn.artifacts?.find((item) => (item.media_type ?? "").startsWith("audio/")) ?? null;
  return <>
    <Field label="Intended">{intended ? <span className="quote">{intended}</span> : <span className="muted">No oracle truth</span>}</Field>
    <Field label="Audio"><ArtifactPlayer artifact={audio} label="caller audio" /></Field>
  </>;
}

function EndpointingCell({ layer, intended }: CellProps) {
  const out = obj(layer?.output);
  const start = num(out.speech_start_ms); const end = num(out.speech_end_ms);
  const segment = text(out.segment_text);
  const truncated = segment !== null && intended !== null && segment.trim().length < intended.trim().length;
  return <>
    <Field label="Speech window">{start !== null || end !== null ? <span className="mono">{start ?? "?"} → {end ?? "?"} ms{start !== null && end !== null ? <span className="muted"> · {end - start} ms</span> : null}</span> : <span className="muted">—</span>}</Field>
    {start !== null && end !== null && <span className="timing-bar"><span style={{ left: `${Math.min(100, (start / Math.max(end, 1)) * 100)}%`, right: 0 }} /></span>}
    {segment !== null && <Field label="Segment to STT"><span className={truncated ? "quote token-bad" : "quote"}>{segment}</span>{truncated && <span className="flag">cut off</span>}</Field>}
    {out.interrupted_bot === true && <span className="flag">barge-in</span>}
    {segment === null && start === null && <KeyValues data={out} />}
    <Settings settings={obj(layer?.settings?.vad ?? layer?.settings)} />
  </>;
}

function SttCell({ layer, intended }: CellProps) {
  const out = obj(layer?.output);
  const interim = Array.isArray(out.interim_hypotheses) ? out.interim_hypotheses.map((item) => display(item)) : [];
  const final = text(out.final_hypothesis) ?? text(out.transcript);
  return <>
    {interim.length > 0 && <Field label="Interim"><ol className="interim">{interim.map((item, index) => <li key={index}>{item}</li>)}</ol></Field>}
    <Field label="Final"><Diffed reference={intended} actual={final} /></Field>
    <Field label="Confidence"><Confidence value={num(out.confidence)} /></Field>
    <Settings settings={layer?.settings} />
  </>;
}

function toolCalls(out: Json): { name: string; args: unknown; result?: unknown }[] {
  if (Array.isArray(out.tool_calls)) return out.tool_calls.map((call) => { const c = obj(call); return { name: text(c.name) ?? "call", args: c.arguments ?? c.args, result: c.result }; });
  if (text(out.tool_name)) return [{ name: text(out.tool_name)!, args: out.arguments }];
  return [];
}

function LlmCell({ layer }: CellProps) {
  const out = obj(layer?.output); const input = obj(layer?.input);
  const calls = toolCalls(out);
  const response = text(out.response_text);
  return <>
    {response && <Field label="Response"><span className="quote">{response}</span></Field>}
    <Field label={`Tool calls · ${calls.length}`}>{calls.length ? <ul className="calls">{calls.map((call, index) => <li key={index}><code className="call-name">{call.name}</code><KeyValues data={obj(call.args)} empty="no arguments" /></li>)}</ul> : <span className="muted">None</span>}</Field>
    {(text(input.context_hash) || num(input.message_count) !== null) && <Field label="Context"><span className="mono small">{num(input.message_count) ?? "?"} msgs · {text(input.context_hash)?.slice(0, 10) ?? "—"}</span></Field>}
    <Settings settings={layer?.settings} />
  </>;
}

function ToolsCell({ layer, turn, layers }: CellProps) {
  const out = obj(layer?.output); const input = obj(layer?.input);
  const results = Array.isArray(out.results) ? out.results.map(obj) : [];
  const calls = Array.isArray(input.calls) ? input.calls.map(obj) : text(input.name) ? [input] : [];
  const before = stateBefore(turn, layers); const after = stateAfter(turn, layers);
  const changed = changedKeys(before, after);
  return <>
    {calls.length > 0 && <Field label={`Calls · ${calls.length}`}><ul className="calls">{calls.map((call, index) => <li key={index}><code className="call-name">{text(call.kind) ?? text(call.name) ?? "call"}{text(call.dataset) ? ` · ${text(call.dataset)}` : text(call.url) ? ` · ${text(call.url)}` : ""}</code>{call.query !== undefined || call.arguments !== undefined ? <KeyValues data={obj(call.query ?? call.arguments)} /> : null}</li>)}</ul></Field>}
    {results.length > 0 && <Field label="Results">{results.map((result, index) => <div key={index} className="result-line"><Badge value={result.ok === false ? "error" : "ok"} tone={result.ok === false ? "bad" : "good"} /><span className="mono small truncate" title={display(result.rows ?? result.response ?? result.error)}>{display(result.rows ?? result.response ?? result.error)}</span></div>)}</Field>}
    <Field label={`State · ${changed.length} changed`}>{changed.length ? <table className="mini"><tbody>{changed.map((key) => <tr key={key}><th>{key}</th><td className="muted">{display(before[key])}</td><td>→</td><td><strong>{display(after[key])}</strong></td></tr>)}</tbody></table> : <span className="muted">No state change</span>}</Field>
    {out.settings !== undefined || layer?.settings?.api_call_mock !== undefined ? <span className="chip">{layer?.settings?.api_call_mock ? "api mock" : "live api"}</span> : null}
  </>;
}

function TtsCell({ layer, turn, layers }: CellProps) {
  const out = obj(layer?.output); const input = obj(layer?.input);
  const source = text(input.text) ?? text(obj(layers.llm?.output).response_text);
  const spoken = text(out.spoken_text);
  const audio = asArtifact(out.audio) ?? (turn.artifacts ?? []).filter((item) => (item.media_type ?? "").startsWith("audio/"))[1] ?? null;
  const drift = source !== null && spoken !== null && source.trim() !== spoken.trim();
  return <>
    <Field label="Text sent">{source ? <span className="quote">{source}</span> : <span className="muted">—</span>}</Field>
    <Field label={drift ? "Spoken · drifted" : "Spoken"}><Diffed reference={source} actual={spoken} /></Field>
    <Field label="Audio"><ArtifactPlayer artifact={audio} label="TTS audio" /></Field>
    <Settings settings={layer?.settings} />
  </>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <div className="field-block"><span className="field-label">{label}</span><div>{children}</div></div>;
}

const renderers: Record<string, (props: CellProps) => ReactElement> = { caller: CallerCell, endpointing: EndpointingCell, speech_to_text: SttCell, llm: LlmCell, tools: ToolsCell, text_to_speech: TtsCell };

function layerSuspect(name: string, layers: Record<string, Layer>, intended: string | null): boolean {
  const out = obj(layers[name]?.output);
  if (name === "speech_to_text") { const final = text(out.final_hypothesis); return !!intended && !!final && final.trim().toLowerCase() !== intended.trim().toLowerCase(); }
  if (name === "endpointing") { const seg = text(out.segment_text); return !!intended && seg !== null && seg.trim().length < intended.trim().length; }
  if (name === "text_to_speech") { const src = text(obj(layers[name]?.input).text); const spoken = text(out.spoken_text); return !!src && !!spoken && src.trim() !== spoken.trim(); }
  if (name === "tools") return Array.isArray(out.results) && out.results.some((item) => obj(item).ok === false);
  return false;
}

function Timeline({ run, provenLayers, focusTurn }: { run: Run; provenLayers: string[]; focusTurn?: number }) {
  const [raw, setRaw] = useState<string>();
  return <div className="timeline">
    <div className="timeline-head" aria-hidden><span>Turn</span>{LAYERS.map((name) => <span key={name} className={provenLayers.includes(name) ? "proven-col" : undefined}>{layerMeta[name].short} {layerMeta[name].label}{provenLayers.includes(name) && <b> · proven</b>}</span>)}</div>
    {run.turns.map((turn, position) => {
      const index = turn.index ?? position;
      const layers = layersOf(turn);
      const intended = text(obj(layers.caller?.output).intended_text);
      return <article key={index} className={`turn-row ${focusTurn === index ? "focus" : ""}`}>
        <div className="turn-label"><strong>T{String(index + 1).padStart(2, "0")}</strong>{turn.node_id && <span className="mono small muted" title="flow node">{turn.node_id}</span>}</div>
        {LAYERS.map((name) => {
          const layer = layers[name];
          const Render = renderers[name];
          const ms = durationMs(layer);
          const suspect = layerSuspect(name, layers, intended);
          const key = `${index}:${name}`;
          return <section key={name} className={`layer-cell ${provenLayers.includes(name) && (focusTurn === undefined || focusTurn === index) ? "proven" : ""} ${suspect ? "suspect" : ""} ${layer ? "" : "absent"}`}>
            <header><span className="cell-title"><span className="mono muted">{layerMeta[name].short}</span> {layerMeta[name].label}</span><span className="cell-meta">{suspect && <i className="dot bad" title="Observed mismatch" />}{ms !== null && <span className="mono muted">{ms} ms</span>}</span></header>
            {layer ? <Render layer={layer} turn={turn} layers={layers} intended={intended} sessionId={run.studio_session_id} /> : <span className="muted small">Layer not recorded</span>}
            {layer && <button className="link-btn" onClick={() => setRaw(raw === key ? undefined : key)}>{raw === key ? "Hide raw" : "Raw"}</button>}
            {raw === key && layer && <Json value={{ input: layer.input, output: layer.output, settings: layer.settings }} />}
          </section>;
        })}
      </article>;
    })}
  </div>;
}

const TRIAL_OPTIONS = [1, 3, 5, 8, 10];

function ArmsTable({ set, proven }: { set: DiagnosisSet; proven?: Diagnosis }) {
  const control = set.control ?? set.diagnoses.find((item) => item.control)?.control;
  const provenSet = new Set(set.diagnoses.filter((item) => item.verdict === "proven").map((item) => item.intervention));
  const sorted = [...set.diagnoses].sort((a, b) => Object.keys(interventionLayer).indexOf(a.intervention) - Object.keys(interventionLayer).indexOf(b.intervention));
  return <div className="table-wrap"><table className="table arms">
    <thead><tr><th>Arm</th><th>Layer replaced</th><th>Pass</th><th>Effect</th><th>Fisher p</th><th>Verdict</th></tr></thead>
    <tbody>
      <tr className="control-row"><td><strong>Control</strong></td><td className="muted">None</td><td><PassBar arm={control} tone="neutral" /></td><td className="mono muted">baseline</td><td className="mono muted">—</td><td><Badge value="control" /></td></tr>
      {sorted.map((item) => <tr key={item.intervention} className={proven?.intervention === item.intervention || provenSet.has(item.intervention) ? "proven-row" : undefined} title={item.summary || undefined}>
        <td><strong>{interventionLabel(item.intervention)}</strong></td>
        <td>{layerMeta[interventionLayer[item.intervention] ?? ""]?.label ?? "—"}</td>
        <td><PassBar arm={item.intervention_arm} /></td>
        <td className={`mono ${item.effect_size > 0 ? "good-text" : item.effect_size < 0 ? "bad-text" : ""}`}>{item.effect_size >= 0 ? "+" : ""}{item.effect_size.toFixed(2)}</td>
        <td className={`mono ${item.fisher_exact_p_value < 0.05 ? "good-text" : ""}`}>{item.fisher_exact_p_value.toFixed(4)}</td>
        <td><Badge value={item.verdict} /></td>
      </tr>)}
    </tbody>
  </table></div>;
}

export function RunDetail({ id }: { id: string }) {
  const run = useAsync(() => getRun(id), [id]);
  const cached = latestDiagnosisFor(id);
  const [set, setSet] = useState<DiagnosisSet | undefined>(cached?.set);
  const [trials, setTrials] = useState(cached?.trials ?? 5);
  const [turnChoice, setTurnChoice] = useState<string>("auto");
  useEffect(() => {
    let alive = true;
    latestReport(id).then((report) => {
      if (!alive || !report || !report.diagnoses.length) return;
      setSet(report);
      if (report.trials) setTrials(report.trials);
    }, () => { /* fall back to the cached report */ });
    return () => { alive = false; };
  }, [id]);
  const [diagnosing, setDiagnosing] = useState(false);
  const [diagError, setDiagError] = useState<unknown>();
  const [proposing, setProposing] = useState(false);
  const [patchError, setPatchError] = useState<unknown>();

  if (run.loading && !run.data) return <div className="page"><Loading label="Loading run…" /></div>;
  if (run.error || !run.data) return <div className="page"><a className="back" href={href.runs()}>← Runs</a><ErrorBox error={run.error ?? new Error("Run not found")} onRetry={run.reload} /></div>;

  const data = run.data;
  const outcome = runOutcome(data);
  const diff = stateDiff(obj(data.expected_database_state), obj(data.final_database_state));
  const proven = provenDiagnosis(set);
  const provenLayer = proven ? interventionLayer[proven.intervention] : undefined;
  const provenLayers = Array.from(new Set([...(set?.proven_layers ?? []), ...(set?.diagnoses ?? []).filter((item) => item.verdict === "proven").map((item) => interventionLayer[item.intervention]).filter(Boolean)]));
  const diagnosedTurn = set?.turn_index ?? proven?.turn_index;
  const spoken = lastSpokenText(data);

  const diagnose = async () => {
    setDiagnosing(true); setDiagError(undefined);
    try {
      const result = await diagnoseAll(id, trials, turnChoice === "auto" ? undefined : Number(turnChoice));
      setSet(result);
      recordDiagnosis({ runId: id, trials, at: new Date().toISOString(), set: result, scenario: data.scenario_id });
    } catch (error) { setDiagError(error); } finally { setDiagnosing(false); }
  };

  const provenArms = (set?.diagnoses ?? []).filter((item) => item.verdict === "proven");
  const propose = async (target: Diagnosis | undefined = proven) => {
    if (!target) return;
    const layer = interventionPatchLayer[target.intervention] ?? target.intervention;
    setProposing(true); setPatchError(undefined);
    try {
      const patch = await proposeVoiceflowPatch(id, layer);
      recordPatch({ patch, runId: id, layer, intervention: target.intervention, at: new Date().toISOString() });
      navigate(href.patch(patch.id));
    } catch (error) { setPatchError(error); } finally { setProposing(false); }
  };

  return <div className="page">
    <a className="back" href={href.runs()}>← Runs</a>
    <PageHeader eyebrow={`RUN · ${shortId(data.id, 12)}`} title={<>{data.scenario_id} <Badge value={outcome.passed ? "passed" : "failed"} /></>} actions={<>
      {data.studio_session_id && <a className="btn ghost" href={studioSessionUrl(data.studio_session_id)} target="_blank" rel="noreferrer">Open in Studio ↗</a>}
      <button className="btn ghost" onClick={run.reload}>Reload</button>
    </>} />

    <section className="meta-bar">
      <div><span className="kicker">SOURCE</span><strong>{data.source ?? "deterministic"}</strong></div>
      <div><span className="kicker">STUDIO SESSION</span><strong className="mono">{data.studio_session_id ? shortId(data.studio_session_id, 12) : "—"}</strong></div>
      <div><span className="kicker">TURNS</span><strong>{data.turns.length}</strong></div>
      <div><span className="kicker">SEED</span><strong className="mono">{data.seed}</strong></div>
      <div><span className="kicker">CONFIG</span><strong className="mono" title={data.agent_config_hash}>{data.agent_config_hash?.slice(0, 10) ?? "—"}</strong></div>
      <div><span className="kicker">CALLER</span><strong>{data.valid_caller_run === false ? <Badge value="invalid" tone="bad" /> : "valid"}</strong></div>
      <div><span className="kicker">CREATED</span><strong>{relativeTime(data.created_at)}</strong></div>
    </section>

    <div className="two-col">
      {data.studio_session_id ? <CallRecording sessionId={data.studio_session_id} synthesized={isSynthesized(data)} /> : <Panel kicker="CALL RECORDING" title="Conversation audio"><Empty title="No Studio session for this run" /></Panel>}
      <RubricsPanel runId={id} refreshKey={set ? JSON.stringify(set).length : 0} />
    </div>

    <div className="two-col">
      <Panel kicker="ORACLE VS ACTUAL" title="Expected state diff">
        {diff.length === 0 ? <Empty title="No expected state recorded" /> : <div className="table-wrap"><table className="table compact">
          <thead><tr><th>Field</th><th>Expected</th><th>Actual</th><th /></tr></thead>
          <tbody>{diff.map((row) => <tr key={row.key} className={row.match === false ? "mismatch-row" : undefined}><td className="mono">{row.key}</td><td className="mono">{row.expected ?? <span className="muted">—</span>}</td><td className={`mono ${row.match === false ? "bad-text" : ""}`}>{row.actual ?? <span className="muted">missing</span>}</td><td>{row.match === null ? <span className="muted small">extra</span> : row.match ? <span className="good-text">✓</span> : <span className="bad-text">✕</span>}</td></tr>)}</tbody>
        </table></div>}
        {outcome.spokenChecks.length > 0 && <div className="spoken-checks"><span className="field-label">Final spoken confirmation</span><span className="quote">{spoken ?? "—"}</span><div className="chips">{outcome.spokenChecks.map((item) => <span key={item.needle} className={`chip ${item.ok ? "good" : "bad"}`}>{item.ok ? "✓" : "✕"} {item.needle}</span>)}</div></div>}
      </Panel>

      <Panel kicker="CAUSAL REPLAY" title="Diagnosis" className="diag-panel" actions={<div className="diag-controls">
        {data.turns.length > 1 && <label className="field inline"><span>Turn</span><select value={turnChoice} onChange={(event) => setTurnChoice(event.target.value)}><option value="auto">Auto</option>{data.turns.map((turn, position) => <option key={position} value={turn.index ?? position}>T{String((turn.index ?? position) + 1).padStart(2, "0")}</option>)}</select></label>}
        <label className="field inline"><span>Trials / arm</span><select value={trials} onChange={(event) => setTrials(Number(event.target.value))}>{TRIAL_OPTIONS.map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <button className="btn primary" onClick={diagnose} disabled={diagnosing}>{diagnosing ? "Replaying…" : set ? "Re-run diagnosis" : "Run causal diagnosis"}</button>
      </div>}>
        {diagnosing && <Loading label={`Replaying control + 5 oracle arms × ${trials} trials…`} />}
        {diagError ? <ErrorBox error={diagError} title={diagError instanceof ApiError && diagError.status === 404 ? "Diagnosis endpoint not available" : undefined} /> : null}
        {!set && !diagnosing && !diagError && <Empty title="Not diagnosed yet">Each arm replays the same trace, seed, config and dataset snapshot with exactly one layer replaced by its oracle.</Empty>}
        {set && !diagnosing && <>
          <div className={`verdict-line ${proven ? "proven" : "open"}`}><span className="verdict-icon">{proven ? "✓" : "?"}</span><p>{verdictSentence(set)}</p><div className="stack right">{set.verdict && <Badge value={set.verdict} tone={set.verdict === "compound" ? "warn" : undefined} />}{proven && <span className="mono small">p = {proven.fisher_exact_p_value.toFixed(4)}</span>}{diagnosedTurn !== undefined && <span className="muted small">turn T{String(diagnosedTurn + 1).padStart(2, "0")}{set.trials ? ` · ×${set.trials}` : ""}{set.created_at ? ` · ${relativeTime(set.created_at)}` : ""}</span>}</div></div>
          {set.diagnoses.length === 0 ? <Empty title="No arms returned" /> : <ArmsTable set={set} proven={proven} />}
          {proven && <div className="next-action">
            <div><span className="kicker">NEXT SAFE ACTION</span><strong>{provenArms.length > 1 ? "Compound failure: propose a bounded patch per proven layer" : `Propose a bounded ${layerMeta[provenLayer ?? ""]?.label ?? proven.intervention} patch`}</strong><span className="muted small">Validated against failing, held-out and regression oracles before any human approval.</span></div>
            {data.source === "voiceflow" ? <div className="page-actions">{(provenArms.length > 1 ? provenArms : [proven]).map((arm) => <button key={arm.intervention} className="btn primary" onClick={() => propose(arm)} disabled={proposing}>{proposing ? "Proposing…" : provenArms.length > 1 ? `${interventionLabel(arm.intervention).replace(" oracle", "")} patch →` : "Propose patch →"}</button>)}</div> : <span className="hint">VoiceFlow runs only. Deterministic runs use the Demo patch flow.</span>}
          </div>}
          {patchError ? <ErrorBox error={patchError} title="Patch proposal failed" /> : null}
        </>}
      </Panel>
    </div>

    <Panel kicker="TRACE" title="Turn-by-turn six-layer timeline" actions={provenLayers.length ? <span className="legend"><i className="swatch proven" /> proven layer <i className="swatch suspect" /> observed mismatch</span> : <span className="legend"><i className="swatch suspect" /> observed mismatch</span>}>
      {data.turns.length === 0 ? <Empty title="This run has no turns" /> : <Timeline run={data} provenLayers={provenLayers} focusTurn={diagnosedTurn} />}
    </Panel>
  </div>;
}


/** True when the trace's audio was synthesized for a simulation rather than recorded on a live call. */
function isSynthesized(run: Run): boolean {
  const turns = (run as unknown as { turns?: Array<{ layers?: Array<{ output?: Record<string, unknown> }> }> }).turns ?? [];
  return turns.some((t) => (t.layers ?? []).some((l) => l.output?.audio_synthesized === true));
}

/** Whole-conversation player: Studio stitches each turn's caller + bot audio into one WAV. */
function CallRecording({ sessionId, synthesized }: { sessionId: string; synthesized: boolean }) {
  const [missing, setMissing] = useState(false);
  const src = `${studioApiUrl}/api/sessions/${encodeURIComponent(sessionId)}/recording.wav`;
  return <Panel kicker="CALL RECORDING" title={synthesized ? "Conversation audio (synthesized voices)" : "Conversation audio"}>
    {missing
      ? <Empty title="No audio for this run" />
      : <div className="call-recording">
          <audio controls preload="metadata" src={src} onError={() => setMissing(true)} style={{ width: "100%" }} />
          <p style={{ margin: "10px 0 0", color: "var(--muted)", fontSize: 13 }}>{synthesized
            ? "Simulated call: caller lines and bot replies rendered with the bot's TTS provider, in turn order."
            : "Caller and bot audio for every turn, in order."} <a href={src} download={`call-${sessionId.slice(0, 8)}.wav`}>Download WAV</a></p>
        </div>}
  </Panel>;
}


const RUBRIC_TONE: Record<RubricStatus, string> = { pass: "good", fail: "bad", warn: "warn", pending: "neutral", not_applicable: "neutral" };
const RUBRIC_ICON: Record<RubricStatus, string> = { pass: "✓", fail: "✕", warn: "!", pending: "…", not_applicable: "–" };

/** The cascading eval: 15 rubrics from caller validity to regression memory, scored from stored evidence. */
function RubricsPanel({ runId, refreshKey }: { runId: string; refreshKey: number }) {
  const rubrics = useAsync(() => getRubrics(runId), [runId, refreshKey]);
  const [open, setOpen] = useState<string | null>(null);
  const counts = rubrics.data?.counts;
  return <Panel kicker="CASCADING EVALUATION" title="Eval rubrics" actions={counts && <span style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
      {(["pass", "warn", "fail", "pending"] as RubricStatus[]).filter((k) => counts[k]).map((k) => <Badge key={k} value={`${counts[k]} ${k}`} tone={RUBRIC_TONE[k]} />)}
    </span>}>
    {rubrics.loading && !rubrics.data ? <Loading /> : rubrics.error ? <ErrorBox error={rubrics.error} /> : !rubrics.data ? <Empty title="No rubric data" /> :
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 2 }}>
        {rubrics.data.rubrics.map((r: Rubric, i: number) => {
          const prev = rubrics.data!.rubrics[i - 1];
          const expanded = open === r.id;
          return <li key={r.id}>
            {(!prev || prev.stage !== r.stage) && <div className="kicker" style={{ margin: i ? "12px 0 4px" : "0 0 4px" }}>{r.stage.toUpperCase()}</div>}
            <button type="button" onClick={() => setOpen(expanded ? null : r.id)} title={r.question}
              style={{ all: "unset", cursor: "pointer", display: "grid", gridTemplateColumns: "22px 1fr auto", gap: 10, alignItems: "start", width: "100%", padding: "7px 8px", borderRadius: 8, boxSizing: "border-box" }}>
              <span className={`badge ${RUBRIC_TONE[r.status]}`} style={{ padding: "0 6px", textAlign: "center" }}>{RUBRIC_ICON[r.status]}</span>
              <span style={{ minWidth: 0 }}>
                <strong style={{ color: "var(--strong)" }}>{r.number}. {r.name}</strong>
                <span style={{ display: "block", color: "var(--body)", fontSize: 13, marginTop: 2 }}>{r.summary}</span>
                {r.details.length > 0 && <span style={{ display: "block", marginTop: 4, fontSize: 12, color: "var(--accent)" }}>
                  {expanded ? "Hide evidence ▴" : `Show evidence (${r.details.length}) ▾`}
                </span>}
                {expanded && r.details.length > 0 && <ul style={{ listStyle: "none", margin: "6px 0 0", padding: 0, display: "grid", gap: 3, fontSize: 12.5 }}>
                  {r.details.map((d, k) => {
                    const mark = d.charAt(0);
                    const tone = mark === "✕" ? "var(--bad)" : mark === "!" ? "var(--warn)" : mark === "✓" ? "var(--accent)" : "var(--muted)";
                    const body = "✓✕!".includes(mark) ? d.slice(1).trim() : d;
                    return <li key={k} style={{ display: "grid", gridTemplateColumns: "14px 1fr", gap: 6, color: "var(--body)" }}>
                      <span style={{ color: tone, fontWeight: 700 }}>{"✓✕!".includes(mark) ? mark : "·"}</span>
                      <span style={{ fontFamily: "var(--mono, ui-monospace, monospace)", wordBreak: "break-word" }}>{body}</span>
                    </li>;
                  })}
                </ul>}
              </span>
              <Badge value={r.status} tone={RUBRIC_TONE[r.status]} />
            </button>
          </li>;
        })}
      </ol>}
  </Panel>;
}
