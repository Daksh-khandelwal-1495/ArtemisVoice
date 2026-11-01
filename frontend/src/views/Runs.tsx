import { FormEvent, useState } from "react";
import { ApiError, importVoiceflowSession, listVoiceflowRuns, recentReports, RunSummary, studioSessionUrl } from "../api";
import { Badge, Empty, ErrorBox, errorMessage, Loading, MissingList, PageHeader, Panel, relativeTime, shortId, useAsync } from "../components/ui";
import { href, navigate } from "../router";

export function Runs() {
  const [includeRefused, setIncludeRefused] = useState(false);
  const runs = useAsync(async (): Promise<RunSummary[]> => {
    const [list, reports] = await Promise.all([listVoiceflowRuns(includeRefused), recentReports(200).catch(() => null)]);
    const latest = new Map<string, { verdict?: string | null; layers?: string[] }>();
    for (const report of reports ?? []) if (report.source_run_id && !latest.has(report.source_run_id)) latest.set(report.source_run_id, { verdict: report.verdict, layers: report.proven_layers });
    return list.map((run) => {
      const report = latest.get(run.id);
      return report ? { ...run, last_verdict: run.last_verdict ?? report.verdict, last_intervention: run.last_intervention ?? (report.layers?.length ? report.layers.join(", ") : null) } : run;
    });
  }, [includeRefused]);
  const [sessionId, setSessionId] = useState("");
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<unknown>();
  const [filter, setFilter] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const id = sessionId.trim();
    if (!id) return;
    setImporting(true); setImportError(undefined);
    try {
      const summary = await importVoiceflowSession(id);
      setSessionId("");
      if (summary.id) navigate(href.run(summary.id)); else runs.reload();
    } catch (error) {
      setImportError(error);
    } finally { setImporting(false); }
  };

  const notReady = importError instanceof ApiError && importError.status === 422 && importError.missing.length > 0;
  const needle = filter.trim().toLowerCase();
  const visible = (runs.data ?? []).filter((run) => !needle || [run.id, run.bot, run.scenario, run.oracle, run.studio_session_id, run.status, run.last_verdict].some((value) => value?.toLowerCase().includes(needle)));

  return <div className="page">
    <PageHeader eyebrow="VOICEFLOW · RUNS" title="Imported Studio sessions">
      Import a replay-grade Studio session. Artemis refuses traces with any missing layer rather than inferring evidence.
    </PageHeader>

    <Panel kicker="IMPORT" title="Import by Studio session id">
      <form className="import-form" onSubmit={submit}>
        <label className="field grow"><span>Studio session id</span><input value={sessionId} onChange={(event) => setSessionId(event.target.value)} placeholder="e.g. 3f2c9a1e-…" spellCheck={false} autoComplete="off" /></label>
        <button className="btn primary" type="submit" disabled={importing || !sessionId.trim()}>{importing ? "Importing…" : "Import trace →"}</button>
      </form>
      {notReady && <div className="callout bad">
        <div><strong>Trace is not replay-ready</strong><span>{(importError as ApiError).message}</span></div>
        <MissingList items={(importError as ApiError).missing} />
        <a className="btn ghost sm" href={studioSessionUrl(sessionId.trim())} target="_blank" rel="noreferrer">Inspect session in Studio ↗</a>
      </div>}
      {importError && !notReady ? <div className="callout bad"><div><strong>Import failed{importError instanceof ApiError && importError.status ? ` · HTTP ${importError.status}` : ""}</strong><span>{errorMessage(importError)}</span></div></div> : null}
    </Panel>

    <Panel kicker="LIBRARY" title={<>Runs {runs.data && <span className="count">{runs.data.length}</span>}</>} actions={<>
      <label className="toggle"><input type="checkbox" checked={includeRefused} onChange={(event) => setIncludeRefused(event.target.checked)} /> Show refused</label>
      <input className="search" value={filter} onChange={(event) => setFilter(event.target.value)} placeholder="Filter…" aria-label="Filter runs" />
      <button className="btn ghost sm" onClick={runs.reload} disabled={runs.loading}>Refresh</button>
    </>}>
      {runs.loading && !runs.data && <Loading label="Loading runs…" />}
      {runs.error ? <ErrorBox error={runs.error} onRetry={runs.reload} /> : null}
      {runs.data && runs.data.length === 0 && <Empty title="No VoiceFlow runs imported">Simulate a Tata demo variant in Studio (Oracles &amp; Demos), then paste its session id above.</Empty>}
      {visible.length > 0 && <div className="table-wrap"><table className="table">
        <thead><tr><th>Run</th><th>Bot</th><th>Scenario / oracle</th><th>Outcome</th><th>Session</th><th>Replay-ready</th><th>Last verdict</th><th>Imported</th><th /></tr></thead>
        <tbody>{visible.map((run) => <tr key={run.id || run.studio_session_id || ""} className={run.id ? "clickable" : undefined} onClick={() => run.id && navigate(href.run(run.id))}>
          <td>{run.id ? <a className="mono" href={href.run(run.id)} onClick={(event) => event.stopPropagation()}>{shortId(run.id)}</a> : <span className="muted small">not imported</span>}</td>
          <td>{run.bot ?? <span className="muted">—</span>}</td>
          <td><div className="stack"><span>{run.scenario ?? "—"}</span>{run.oracle && <span className="muted small mono">{run.oracle}</span>}</div></td>
          <td>{run.passed === true ? <Badge value="passed" /> : run.passed === false ? <Badge value="failed" /> : <span className="muted">—</span>}</td>
          <td><div className="stack"><span className="small">{run.status ?? "—"}{run.trace_source ? <span className="muted"> · {run.trace_source}</span> : null}</span>{run.end_node && <span className="muted small mono">{run.end_node}</span>}</div></td>
          <td>{run.replay_ready === null || run.replay_ready === undefined ? <span className="muted">—</span> : run.replay_ready ? <Badge value="ready" tone="good" /> : <span title={run.missing.join("\n")}><Badge value={`missing ${run.missing.length}`} tone="bad" /></span>}</td>
          <td><div className="stack"><Badge value={run.last_verdict} />{run.last_intervention && <span className="muted small">{run.last_intervention.replaceAll("_", " ")}</span>}</div></td>
          <td className="muted nowrap">{relativeTime(run.created_at)}</td>
          <td className="right">{run.studio_session_id && <a className="btn ghost sm" href={studioSessionUrl(run.studio_session_id)} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>Open in Studio ↗</a>}</td>
        </tr>)}</tbody>
      </table></div>}
      {runs.data && runs.data.length > 0 && visible.length === 0 && <Empty title="No runs match the filter" />}
    </Panel>
  </div>;
}
