import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, Arm, artifactUrl, Artifact } from "../api";

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Unknown error";
}

/** Fetch-on-mount helper with explicit loading / error / data states. */
export function useAsync<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<unknown>();
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const reload = useCallback(() => {
    const id = ++seq.current;
    setLoading(true);
    setError(undefined);
    load().then(
      (value) => { if (id === seq.current) { setData(value); setLoading(false); } },
      (cause) => { if (id === seq.current) { setError(cause); setLoading(false); } },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { reload(); }, [reload]);
  return { data, error, loading, reload, setData };
}

export function PageHeader({ eyebrow, title, children, actions }: { eyebrow: string; title: ReactNode; children?: ReactNode; actions?: ReactNode }) {
  return <header className="page-header">
    <div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1>{children && <p className="page-lede">{children}</p>}</div>
    {actions && <div className="page-actions">{actions}</div>}
  </header>;
}

export function Panel({ title, kicker, actions, children, className = "" }: { title?: ReactNode; kicker?: string; actions?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}>
    {(title || actions) && <div className="panel-head"><div>{kicker && <span className="kicker">{kicker}</span>}{title && <h2>{title}</h2>}</div>{actions && <div className="panel-actions">{actions}</div>}</div>}
    {children}
  </section>;
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return <div className="state-box loading" role="status"><span className="spinner" aria-hidden />{label}</div>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return <div className="state-box empty-state"><span className="empty-orb" aria-hidden>◌</span><strong>{title}</strong>{children && <p>{children}</p>}</div>;
}

export function ErrorBox({ error, title, onRetry }: { error: unknown; title?: string; onRetry?: () => void }) {
  const status = error instanceof ApiError ? error.status : undefined;
  const heading = title ?? (status === 0 ? "Artemis API unavailable" : status === 404 ? "Not found" : status === 501 ? "Not implemented by the backend yet" : "Request failed");
  return <div className="state-box error-state" role="alert">
    <strong>{heading}{status ? <span className="mono muted"> · HTTP {status}</span> : null}</strong>
    <span>{errorMessage(error)}</span>
    {status === 0 && <code>uvicorn artemisVoice.Evals.api.app:app --port 8001 --reload</code>}
    {error instanceof ApiError && error.missing.length > 0 && <MissingList items={error.missing} />}
    {onRetry && <button className="btn ghost sm" onClick={onRetry}>Retry</button>}
  </div>;
}

export function MissingList({ items, title = "Missing trace evidence" }: { items: string[]; title?: string }) {
  return <div className="missing-list"><span className="kicker">{title} · {items.length}</span><ul>{items.map((item, index) => <li key={`${item}-${index}`}><i aria-hidden />{item}</li>)}</ul></div>;
}

const verdictTone: Record<string, string> = { proven: "good", likely: "warn", inconclusive: "neutral", not_the_agent: "info", compound: "warn", passed: "good", failed: "bad", approved: "good", pending: "warn", rejected: "bad", validated: "info", control: "neutral" };

export function Badge({ value, tone }: { value: string | null | undefined; tone?: string }) {
  if (!value) return <span className="muted">—</span>;
  return <span className={`badge ${tone ?? verdictTone[value.toLowerCase()] ?? "neutral"}`}>{value.replaceAll("_", " ")}</span>;
}

export function PassBar({ arm, tone }: { arm: Arm | undefined; tone?: "good" | "bad" | "neutral" }) {
  if (!arm || !arm.trials) return <span className="muted">—</span>;
  const pct = Math.max(0, Math.min(1, arm.pass_count / arm.trials));
  const resolvedTone = tone ?? (pct >= 0.8 ? "good" : pct <= 0.2 ? "bad" : "neutral");
  return <span className="passbar" title={`${arm.pass_count} of ${arm.trials} replays passed`}>
    <span className="passbar-track"><span className={`passbar-fill ${resolvedTone}`} style={{ width: `${pct * 100}%` }} /></span>
    <span className="mono">{arm.pass_count}/{arm.trials}</span>
  </span>;
}

export function RateDelta({ before, after }: { before?: number; after?: number }) {
  const fmt = (value?: number) => (typeof value === "number" && Number.isFinite(value) ? `${Math.round(value * 100)}%` : "—");
  const delta = typeof before === "number" && typeof after === "number" ? after - before : undefined;
  return <span className="rate-delta mono"><span>{fmt(before)}</span><span className="arrow">→</span><strong>{fmt(after)}</strong>{delta !== undefined && <em className={delta > 0 ? "good" : delta < 0 ? "bad" : "muted"}>{delta > 0 ? "+" : ""}{Math.round(delta * 100)} pt</em>}</span>;
}

export function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  try { return JSON.stringify(value); } catch { return String(value); }
}

export function KeyValues({ data, empty = "No data recorded" }: { data: Record<string, unknown> | undefined | null; empty?: string }) {
  const entries = Object.entries(data ?? {});
  if (!entries.length) return <span className="muted small">{empty}</span>;
  return <dl className="kv">{entries.map(([key, value]) => <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{display(value)}</dd></div>)}</dl>;
}

export function Json({ value }: { value: unknown }) {
  let text: string;
  try { text = JSON.stringify(value, null, 2) ?? "null"; } catch { text = String(value); }
  return <pre className="json">{text}</pre>;
}

export function ArtifactPlayer({ artifact, label }: { artifact: Artifact | null | undefined; label: string }) {
  const [failed, setFailed] = useState(false);
  if (!artifact?.uri) return <span className="artifact missing-artifact">No {label} artifact</span>;
  const url = artifactUrl(artifact.uri);
  const isAudio = (artifact.media_type ?? "").startsWith("audio/");
  const meta = <span className="artifact-meta mono" title={artifact.uri}>{artifact.media_type ?? "unknown"}{artifact.bytes ? ` · ${(artifact.bytes / 1024).toFixed(1)} kB` : ""}{artifact.sha256 ? ` · ${artifact.sha256.slice(0, 10)}` : ""}</span>;
  if (!url) return <span className="artifact">{meta}<span className="muted small">not fetchable ({artifact.uri.split(":")[0]})</span></span>;
  if (isAudio && !failed) return <span className="artifact"><audio controls preload="none" src={url} onError={() => setFailed(true)} aria-label={label} />{meta}</span>;
  return <span className="artifact">{meta}<a href={url} target="_blank" rel="noreferrer">{failed ? "Audio unavailable · open" : "Open artifact"} ↗</a></span>;
}

export function relativeTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return date.toLocaleDateString();
}

export function shortId(id: string | null | undefined, length = 8) {
  return id ? (id.length > length + 2 ? id.slice(0, length) : id) : "—";
}
