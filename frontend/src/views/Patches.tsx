import { useState } from "react";
import { approvePatch, getPatch, listRegressions, layerMeta, interventionLayer, listPatches, PatchGateResult, PatchProposal, patchParams, patchType, rememberPatch, studioSessionUrl, validatePatch } from "../api";
import { Badge, display, Empty, ErrorBox, Json, KeyValues, Loading, PageHeader, Panel, RateDelta, relativeTime, shortId, useAsync } from "../components/ui";
import { href } from "../router";
import { getPatchRecord, listPatchRecords, PatchRecord, recordPatch, updatePatch } from "../store";

const patchCopy: Record<string, string> = {
  stt_keyterm_boost: "Boost the misheard expected value as an STT keyterm so the recogniser prefers it.",
  stt_keyword_boost: "Boost the misheard expected value as an STT keyword.",
  confirmation_readback: "Read captured values back to the caller and accept corrections before committing.",
  stricter_validation: "Reject tool-call arguments that do not match the field's expected pattern.",
  fallback_route: "Route to a safe fallback when the lookup or API call fails or returns no match.",
  vad_stop_secs: "Wait longer before deciding the caller has finished speaking.",
};

const layerLabel = (value: string | null | undefined) => (value ? layerMeta[value]?.label ?? layerMeta[interventionLayer[value] ?? ""]?.label ?? value : "—");

const setLabel: Record<string, string> = { failing: "Failing case", held_out: "Held-out variants", regression: "Regression suite" };

export function Patches() {
  const remote = useAsync(listPatches, []);
  const local = listPatchRecords();
  const merged = new Map<string, { patch: PatchProposal; record?: PatchRecord; gate?: PatchGateResult | null }>();
  for (const record of local) merged.set(record.patch.id, { patch: record.patch, record, gate: record.gate });
  for (const item of remote.data ?? []) {
    const previous = merged.get(item.patch.id);
    merged.set(item.patch.id, { patch: { ...previous?.patch, ...item.patch }, record: previous?.record, gate: item.gate ?? previous?.gate });
  }
  const rows = Array.from(merged.values()).sort((a, b) => String(b.patch.created_at ?? b.record?.at ?? "").localeCompare(String(a.patch.created_at ?? a.record?.at ?? "")));

  return <div className="page">
    <PageHeader eyebrow="REPAIR · PATCHES" title="Bounded patch proposals">
      Patches follow only from a proven diagnosis, are validated on failing, held-out and regression oracles, and reach a published flow only after a named human approves.
    </PageHeader>
    <Panel kicker="QUEUE" title={<>Proposals <span className="count">{rows.length}</span></>} actions={<button className="btn ghost sm" onClick={remote.reload} disabled={remote.loading}>Refresh</button>}>
      {remote.loading && rows.length === 0 && <Loading label="Loading patches…" />}
      {remote.error ? <ErrorBox error={remote.error} title="Could not list patches from the API" onRetry={remote.reload} /> : null}
      {!remote.loading && rows.length === 0 && <Empty title="No patch proposals">Open a run, run a causal diagnosis, and choose “Propose patch” on a proven layer.</Empty>}
      {rows.length > 0 && <div className="table-wrap"><table className="table">
        <thead><tr><th>Patch</th><th>Type</th><th>Layer</th><th>Source run</th><th>Gate</th><th>Status</th><th>Proposed</th></tr></thead>
        <tbody>{rows.map(({ patch, record, gate }) => {
          const runId = patch.source_run_id ?? patch.diagnosis_source_run_id ?? record?.runId;
          return <tr key={patch.id}>
            <td><a className="mono" href={href.patch(patch.id)}>{shortId(patch.id)}</a></td>
            <td className="mono">{patchType(patch)}</td>
            <td>{layerLabel(patch.layer ?? record?.layer)}{patch.source && patch.source !== "voiceflow" ? <span className="muted small"> · {patch.source}</span> : null}</td>
            <td>{runId ? <a className="mono" href={href.run(runId)}>{shortId(runId)}</a> : "—"}</td>
            <td>{gate ? <Badge value={gate.passed ? "passed" : "failed"} /> : <span className="muted small">not validated</span>}</td>
            <td><Badge value={patch.status ?? "pending"} /></td>
            <td className="muted nowrap">{relativeTime(patch.created_at ?? record?.at)}</td>
          </tr>;
        })}</tbody>
      </table></div>}
      {remote.data === null && rows.length > 0 && <p className="muted small note">The API has no patch listing endpoint yet; showing proposals created from this browser.</p>}
    </Panel>
  </div>;
}

export function PatchDetail({ id }: { id: string }) {
  const initial = getPatchRecord(id);
  const remote = useAsync(() => getPatch(id), [id]);
  const [patchState, setPatchState] = useState<PatchProposal | undefined>(initial?.patch);
  const [gateState, setGate] = useState<PatchGateResult | undefined>(initial?.gate);
  const [regressionName, setRegressionName] = useState("");
  const [remembered, setRemembered] = useState<Record<string, unknown> | boolean | undefined>(initial?.remembered);
  const [busy, setBusy] = useState<"validate" | "approve" | "remember">();
  const regressions = useAsync(() => listRegressions().catch(() => []), [id]);
  const savedRegression = regressions.data?.find((item) => item.patch_id === id);
  const rememberedState = remembered ?? (savedRegression ? savedRegression : undefined);
  const [error, setError] = useState<{ action: string; error: unknown }>();
  const [approver, setApprover] = useState("");

  const patch: PatchProposal | undefined = remote.data ? { ...remote.data.patch, ...(patchState?.status === "approved" ? patchState : {}) } : patchState;
  const gate: PatchGateResult | undefined = gateState ?? remote.data?.gate ?? undefined;
  if (!patch && remote.loading) return <div className="page"><Loading label="Loading patch…" /></div>;
  if (!patch) return <div className="page"><a className="back" href={href.patches()}>← Patches</a>{remote.error ? <ErrorBox error={remote.error} onRetry={remote.reload} /> : <Empty title="Patch not found">No patch with this id exists in the Artemis store.</Empty>}</div>;

  const type = patchType(patch);
  const params = patchParams(patch);
  const status = patch.status ?? "pending";
  const runId = patch.source_run_id ?? patch.diagnosis_source_run_id ?? initial?.runId;
  const layerKey = initial?.intervention ? interventionLayer[initial.intervention] : undefined;
  const approved = status === "approved";
  const canApprove = !!gate?.passed && !approved && approver.trim().length >= 2;
  const isVoiceflow = (patch.source ?? "voiceflow") === "voiceflow";
  const canRemember = !!gate?.passed && (approved || !isVoiceflow) && !rememberedState;
  const graphDiff = patch.studio_graph_diff ?? patch.graph_diff;

  const persist = (update: Partial<PatchRecord>) => {
    if (getPatchRecord(id)) updatePatch(id, update);
    else recordPatch({ patch: update.patch ?? patch, runId: runId ?? "", layer: patch.layer ?? "", at: new Date().toISOString(), ...update });
  };

  const act = async (action: "validate" | "approve" | "remember") => {
    setBusy(action); setError(undefined);
    try {
      if (action === "validate") { const result = await validatePatch(id); setGate(result); persist({ gate: result }); }
      if (action === "approve") { const result = await approvePatch(id, approver.trim()); const next = { ...patch, ...result, status: result?.status ?? "approved", approved_by: result?.approved_by ?? approver.trim() }; setPatchState(next); remote.setData({ patch: next, gate: gate ?? null }); persist({ patch: next }); }
      if (action === "remember") { const result = await rememberPatch(id, regressionName.trim() || undefined); setRemembered(result ?? true); persist({ remembered: true }); }
    } catch (cause) { setError({ action, error: cause }); } finally { setBusy(undefined); }
  };

  const steps = [
    { label: "Proposed", done: true },
    { label: "Validated", done: !!gate, bad: gate ? !gate.passed : false },
    { label: "Approved", done: approved },
    { label: "Remembered", done: !!rememberedState },
  ];

  return <div className="page">
    <a className="back" href={href.patches()}>← Patches</a>
    <PageHeader eyebrow={`PATCH · ${shortId(patch.id, 12)}`} title={<><span className="mono">{type}</span> <Badge value={status} /></>} actions={runId ? <a className="btn ghost" href={href.run(runId)}>Source run →</a> : undefined} />

    <ol className="stepper">{steps.map((step, index) => <li key={step.label} className={step.bad ? "bad" : step.done ? "done" : ""}><span>{step.bad ? "✕" : step.done ? "✓" : index + 1}</span>{step.label}</li>)}</ol>

    <div className="two-col">
      <Panel kicker="PROPOSAL" title="Bounded change">
        <p className="muted small">{patchCopy[type] ?? "A bounded configuration change tied to a proven causal diagnosis."}</p>
        {patch.rationale && <p className="small">{patch.rationale}</p>}
        <dl className="kv wide">
          <div><dt>type</dt><dd>{type}</dd></div>
          <div><dt>layer</dt><dd>{layerKey ? layerMeta[layerKey]?.label : layerLabel(patch.layer ?? initial?.layer)}</dd></div>
          <div><dt>source run</dt><dd>{runId ? <a href={href.run(runId)}>{shortId(runId, 12)}</a> : "—"}</dd></div>
          <div><dt>studio session</dt><dd>{patch.studio_session_id ? <a href={studioSessionUrl(patch.studio_session_id)} target="_blank" rel="noreferrer">{shortId(patch.studio_session_id, 12)} ↗</a> : "—"}</dd></div>
          <div><dt>draft candidate</dt><dd title={patch.studio_candidate_id ?? undefined}>{shortId(patch.studio_candidate_id ?? patch.candidate_version_id, 12)}</dd></div>
          {patch.approved_by && <div><dt>approved by</dt><dd>{patch.approved_by}{patch.approved_at ? ` · ${relativeTime(patch.approved_at)}` : ""}</dd></div>}
        </dl>
        <span className="field-label">Parameters</span>
        <KeyValues data={params} empty="No parameters" />
        {patch.alternatives && patch.alternatives.length > 0 && <><span className="field-label" style={{ marginTop: 12 }}>Alternatives considered</span><div className="alt-list">{patch.alternatives.map((alt, index) => <span key={index} className="chip" title={display(alt.params)}>{alt.type ?? "patch"}</span>)}</div></>}
        {graphDiff !== undefined && graphDiff !== null && <details className="raw"><summary>Flow graph diff (draft)</summary><Json value={graphDiff} /></details>}
        {patch.promotion && <details className="raw"><summary>Studio promotion</summary><Json value={patch.promotion} /></details>}
      </Panel>

      <Panel kicker="GATE" title="Validation" actions={<button className="btn primary" onClick={() => act("validate")} disabled={!!busy || approved}>{busy === "validate" ? "Validating…" : gate ? "Re-validate" : "Validate"}</button>}>
        {busy === "validate" && <Loading label="Replaying failing, held-out and regression oracles against the candidate…" />}
        {error?.action === "validate" && <ErrorBox error={error.error} title="Validation failed to run" />}
        {!gate && busy !== "validate" && !error && <Empty title="Not validated">The patch must improve the failing case and held-out variants without regressing saved regressions.</Empty>}
        {gate && busy !== "validate" && <>
          <div className={`verdict-line ${gate.passed ? "proven" : "failed"}`}><span className="verdict-icon">{gate.passed ? "✓" : "✕"}</span><p>{gate.passed ? "Gate passed: the patch fixes the failure without regressions." : gate.failure_reason ?? "Gate failed."}</p></div>
          {gate.sets.length === 0 ? <Empty title="No validation sets returned" /> : <div className="table-wrap"><table className="table compact">
            <thead><tr><th>Set</th><th>Before → after</th><th>Oracles</th></tr></thead>
            <tbody>{gate.sets.map((item) => <tr key={item.name}>
              <td><strong>{setLabel[item.name] ?? item.name.replaceAll("_", " ")}</strong></td>
              <td><RateDelta before={item.before_pass_rate ?? item.before} after={item.after_pass_rate ?? item.after} /></td>
              <td className="mono muted">{Array.isArray(item.cases) ? (item.cases.length ? item.cases.length : <span title="skipped">0 · skipped</span>) : item.total ?? "—"}</td>
            </tr>)}</tbody>
          </table></div>}
          {gate.warnings && gate.warnings.length > 0 && <ul className="warnings">{gate.warnings.map((item) => <li key={item}>{item}</li>)}</ul>}
          {gate.sets.some((item) => Array.isArray(item.cases) && item.cases.length) && <details className="raw" open><summary>Per-oracle results</summary><div className="table-wrap" style={{ marginTop: 8 }}><table className="table compact">
            <thead><tr><th>Set</th><th>Oracle</th><th>Base</th><th>Candidate</th><th>Sessions</th></tr></thead>
            <tbody>{gate.sets.flatMap((item) => (Array.isArray(item.cases) ? item.cases : []).map((raw, index) => {
              const c = (raw ?? {}) as Record<string, unknown>;
              const pass = (value: unknown) => value === true ? <span className="good-text">pass</span> : value === false ? <span className="bad-text">fail</span> : <span className="muted">—</span>;
              const sess = (value: unknown) => typeof value === "string" && value ? <a href={studioSessionUrl(value)} target="_blank" rel="noreferrer" className="mono">{shortId(value)}</a> : <span className="muted">—</span>;
              return <tr key={`${item.name}-${index}`}><td>{setLabel[item.name] ?? item.name}</td><td className="mono">{display(c.oracle_id)}</td><td>{pass(c.before_passed)}</td><td>{pass(c.after_passed)}</td><td>{sess(c.before_session_id)} → {sess(c.after_session_id)}</td></tr>;
            }))}</tbody>
          </table></div></details>}
        </>}
      </Panel>
    </div>

    <div className="two-col">
      <Panel kicker="HUMAN APPROVAL" title="Approve and publish">
        <p className="muted small">Approval promotes the draft candidate to the published flow in Studio. Type your name to sign the change.</p>
        <form className="import-form" onSubmit={(event) => { event.preventDefault(); if (canApprove) act("approve"); }}>
          <label className="field grow"><span>Approver name</span><input value={approver} onChange={(event) => setApprover(event.target.value)} placeholder={approved ? patch.approved_by ?? "Approved" : "Full name"} disabled={approved || !gate?.passed} autoComplete="name" /></label>
          <button className="btn primary" type="submit" disabled={!canApprove || !!busy}>{busy === "approve" ? "Approving…" : approved ? "Approved" : "Approve"}</button>
        </form>
        {!gate?.passed && !approved && <p className="hint">{gate ? "Approval is locked: the validation gate did not pass." : "Approval unlocks after the validation gate passes."}</p>}
        {error?.action === "approve" && <ErrorBox error={error.error} title="Approval failed" />}
        {approved && <div className="callout good"><div><strong>Approved{patch.approved_by ? ` by ${patch.approved_by}` : ""}</strong><span>The candidate flow version was promoted.</span></div></div>}
      </Panel>

      <Panel kicker="MEMORY" title="Remember as regression">
        <p className="muted small">Save the originally failing call as a regression oracle so every future patch is checked against it.</p>
        <form className="import-form" onSubmit={(event) => { event.preventDefault(); if (canRemember) act("remember"); }}>
          <label className="field grow"><span>Regression name (optional)</span><input value={regressionName} onChange={(event) => setRegressionName(event.target.value)} placeholder="Defaults to the oracle name" disabled={!canRemember} /></label>
          <button className="btn ghost" type="submit" disabled={!canRemember || !!busy}>{busy === "remember" ? "Saving…" : rememberedState ? "Saved as regression ✓" : "Remember as regression"}</button>
        </form>
        {!rememberedState && !gate?.passed && <p className="hint">Requires a passed validation gate.</p>}
        {!rememberedState && gate?.passed && isVoiceflow && !approved && <p className="hint">Requires human approval first.</p>}
        {error?.action === "remember" && <ErrorBox error={error.error} title="Could not save regression" />}
        {rememberedState && typeof rememberedState === "object" && (() => { const r = rememberedState as Record<string, unknown>; const oracle = (r.oracle ?? {}) as Record<string, unknown>; return <div className="callout good"><div><strong>Saved as regression</strong><span>{display(oracle.name ?? r.studio_oracle_id ?? r.id)}</span></div><dl className="kv"><div><dt>studio oracle</dt><dd>{display(r.studio_oracle_id)}</dd></div><div><dt>slug</dt><dd>{display(oracle.slug)}</dd></div><div><dt>case id</dt><dd>{shortId(typeof r.id === "string" ? r.id : null, 12)}</dd></div></dl></div>; })()}
      </Panel>
    </div>
  </div>;
}
