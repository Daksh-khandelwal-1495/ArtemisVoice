import { readiness, ReadinessCheck, recentReports, studioSessionUrl, studioUiUrl, verdictSentence, interventionLabel, provenDiagnosis, Diagnosis, DiagnosisSet } from "../api";
import { Badge, Empty, ErrorBox, Loading, MissingList, PageHeader, Panel, relativeTime, shortId, useAsync } from "../components/ui";
import { href } from "../router";
import { recentDiagnoses } from "../store";

function ReadinessCard({ check }: { check: ReadinessCheck }) {
  const tone = check.ok === null ? "unknown" : check.ok ? "ok" : "bad";
  return <article className={`ready-card ${tone}`}>
    <div className="ready-top"><span className="kicker">{check.label}</span><i className={`dot ${tone}`} aria-label={tone} /></div>
    <strong className="ready-value">{check.value}</strong>
    <span className="ready-detail" title={check.detail}>{check.detail ?? (check.ok === null ? "Not reported by /readiness" : " ")}</span>
  </article>;
}

type Row = { runId: string; at: string; diagnosis?: Diagnosis; sentence: string; scenario?: string | null; verdict?: string | null; trials?: number };

const topArm = (set: DiagnosisSet) => provenDiagnosis(set) ?? [...set.diagnoses].sort((a, b) => b.effect_size - a.effect_size)[0];

export function Overview() {
  const ready = useAsync(readiness, []);
  const reports = useAsync(() => recentReports(20), []);
  const local = recentDiagnoses();
  const scenarioByRun = new Map(local.map((record) => [record.runId, record.scenario]));
  const rows: Row[] = reports.data && reports.data.length
    ? reports.data.map((set) => ({ runId: set.source_run_id ?? "", at: set.created_at ?? "", diagnosis: topArm(set), sentence: verdictSentence(set), verdict: set.verdict, trials: set.trials, scenario: scenarioByRun.get(set.source_run_id ?? "") }))
    : local.map((record) => ({ runId: record.runId, at: record.at, diagnosis: topArm(record.set), sentence: verdictSentence(record.set), scenario: record.scenario, verdict: record.set.verdict, trials: record.trials }));

  return <div className="page">
    <PageHeader eyebrow="ARTEMIS · OVERVIEW" title="System readiness" actions={<><button className="btn ghost" onClick={() => { ready.reload(); reports.reload(); }} disabled={ready.loading}>{ready.loading ? "Checking…" : "Refresh"}</button><a className="btn primary" href={href.runs()}>Import a session →</a></>}>
      Everything a causal replay needs: configured providers, a healthy trace store, a reachable Studio, and complete six-layer evidence.
    </PageHeader>

    {ready.loading && !ready.data && <Loading label="Checking /readiness…" />}
    {ready.error ? <ErrorBox error={ready.error} title="Readiness unavailable" onRetry={ready.reload} /> : null}
    {ready.data && <>
      <section className="ready-grid">
        <ReadinessCard check={ready.data.providers} />
        <ReadinessCard check={ready.data.database} />
        <ReadinessCard check={ready.data.studio} />
        <ReadinessCard check={ready.data.replayReady} />
        <ReadinessCard check={ready.data.missing} />
      </section>
      {ready.data.missingByRun.length > 0 && <Panel kicker="TRACE GAPS" title="Runs with missing artifacts">
        <div className="gap-grid">{ready.data.missingByRun.map((item) => <div key={item.run} className="gap-item">{item.runId ? <a className="mono" href={href.run(item.runId)}>run {shortId(item.runId, 12)}</a> : item.sessionId ? <a className="mono" href={studioSessionUrl(item.sessionId)} target="_blank" rel="noreferrer">session {shortId(item.sessionId, 12)} ↗ <span className="muted">(refused)</span></a> : <span className="mono">{item.run}</span>}<MissingList items={item.missing} title="Missing" /></div>)}</div>
      </Panel>}
    </>}

    <Panel kicker="EVIDENCE" title="Recent diagnoses" actions={<a className="btn ghost sm" href={studioUiUrl} target="_blank" rel="noreferrer">Open Studio ↗</a>}>
      {reports.loading && rows.length === 0 && <Loading label="Loading recent diagnoses…" />}
      {reports.error ? <ErrorBox error={reports.error} title="Could not load /diagnoses" onRetry={reports.reload} /> : null}
      {!reports.loading && rows.length === 0 ? <Empty title="No diagnoses yet">Import a replay-ready Studio session, open it, and run a causal diagnosis.</Empty> : rows.length === 0 ? null :
        <div className="table-wrap"><table className="table">
          <thead><tr><th>Run</th><th>Report</th><th>Top arm</th><th>Arm verdict</th><th>Effect</th><th>p</th><th>Finding</th><th>When</th></tr></thead>
          <tbody>{rows.slice(0, 10).map((row, index) => <tr key={`${row.runId}-${row.at}-${index}`}>
            <td>{row.runId ? <a className="mono" href={href.run(row.runId)}>{row.scenario ?? shortId(row.runId)}</a> : "—"}</td>
            <td><Badge value={row.verdict} />{row.trials ? <span className="muted small"> ×{row.trials}</span> : null}</td>
            <td>{row.diagnosis ? interventionLabel(row.diagnosis.intervention) : "—"}</td>
            <td><Badge value={row.diagnosis?.verdict} /></td>
            <td className="mono">{row.diagnosis ? `${row.diagnosis.effect_size >= 0 ? "+" : ""}${row.diagnosis.effect_size.toFixed(2)}` : "—"}</td>
            <td className="mono">{row.diagnosis ? row.diagnosis.fisher_exact_p_value.toFixed(4) : "—"}</td>
            <td className="finding">{row.sentence}</td>
            <td className="muted nowrap">{relativeTime(row.at)}</td>
          </tr>)}</tbody>
        </table></div>}
    </Panel>
  </div>;
}
