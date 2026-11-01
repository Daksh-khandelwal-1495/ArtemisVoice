"use client";

import {
  AlertTriangle,
  AudioLines,
  Bot as BotIcon,
  CheckCircle2,
  Download,
  ExternalLink,
  FileJson,
  FileText,
  Link2,
  Mic,
  RefreshCw,
  ScanText,
  Scissors,
  Sparkles,
  Volume2,
  Wrench,
  XCircle,
} from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";
import useSWR from "swr";
import { Badge, Button, Card, EmptyState, ErrorBox, SectionTitle, Select, Spinner, cn, useToast } from "@/components/ui";
import { ApiError, api, artemisImportUrl, artifactUrl, downloadJson, formatDetail } from "@/lib/api";
import { stateDiff, wordDiff } from "@/lib/diff";
import { faultLayers } from "@/lib/oracle";
import type { SessionTrace, TestOracle, TraceArtifact, TraceLayerName, TraceTurn } from "@/lib/types";
import { ReplayPanel } from "./replay-panel";

const str = (v: unknown) => (v == null ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));
export const shortSha = (sha?: string) => (sha ? `${sha.slice(0, 8)}…${sha.slice(-4)}` : "—");
export const fmtBytes = (n?: number) => (n == null ? "—" : n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(2)} MB`);
const fileOf = (uri: string) => uri.replace(/^artifact:\/\/[^/]+\//, "");

const LAYER_META: Record<TraceLayerName, { label: string; icon: ReactNode }> = {
  caller: { label: "Caller", icon: <Mic className="size-3.5" /> },
  endpointing: { label: "Endpointing", icon: <Scissors className="size-3.5" /> },
  speech_to_text: { label: "STT", icon: <ScanText className="size-3.5" /> },
  llm: { label: "LLM", icon: <Sparkles className="size-3.5" /> },
  tools: { label: "Tools", icon: <Wrench className="size-3.5" /> },
  text_to_speech: { label: "TTS", icon: <Volume2 className="size-3.5" /> },
};

/* ------------------------------------------------------------- primitives */

export function ArtifactPlayer({ a, label }: { a: TraceArtifact | null | undefined; label: string }) {
  if (!a) return <div className="text-[11px] italic text-subtle">no {label} artifact</div>;
  const url = artifactUrl(a.uri);
  return (
    <div className="space-y-1">
      {a.media_type.startsWith("audio/") ? (
        <audio controls preload="none" src={url} className="h-8 w-full" />
      ) : (
        <a href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[11.5px] text-brand hover:underline">
          <FileText className="size-3" /> {fileOf(a.uri)}
        </a>
      )}
      <div className="flex flex-wrap gap-x-2 font-mono text-[10px] text-subtle" title={a.sha256}>
        <span>sha256 {shortSha(a.sha256)}</span>
        <span>{fmtBytes(a.bytes)}</span>
        <span>{a.media_type}</span>
      </div>
    </div>
  );
}

/** Inline word diff: `mode="source"` marks deletions, `mode="target"` marks insertions. */
export function DiffText({ a, b, mode }: { a: string; b: string; mode: "source" | "target" }) {
  const ops = wordDiff(a, b);
  return (
    <span>
      {ops.map((o, i) =>
        o.op === "same" ? (
          <span key={i}>{o.text}</span>
        ) : o.op === "del" && mode === "source" ? (
          <mark key={i} className="rounded bg-red-500/15 px-0.5 text-red-700 line-through decoration-red-500/60 dark:text-red-300">
            {o.text}
          </mark>
        ) : o.op === "add" && mode === "target" ? (
          <mark key={i} className="rounded bg-amber-500/20 px-0.5 font-medium text-amber-800 dark:text-amber-300">
            {o.text}
          </mark>
        ) : null,
      )}
    </span>
  );
}

function KV({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="flex gap-2 text-[11.5px]">
      <span className="w-20 shrink-0 text-muted">{k}</span>
      <span className="min-w-0 flex-1 break-words">{children}</span>
    </div>
  );
}

function Quote({ children, tone }: { children: ReactNode; tone?: "brand" }) {
  return <div className={cn("rounded-md px-2 py-1 text-[12px] leading-relaxed", tone === "brand" ? "bg-brand/10" : "bg-panel-2")}>{children}</div>;
}

function LayerCard({ name, present, children, timing }: { name: TraceLayerName; present: boolean; children: ReactNode; timing?: string }) {
  const m = LAYER_META[name];
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5 rounded-md border p-2.5", present ? "border-border bg-panel" : "border-dashed border-red-500/40 bg-red-500/5")}>
      <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted">
        {m.icon}
        {m.label}
        {timing && <span className="ml-auto font-mono text-[10px] font-normal normal-case tracking-normal text-subtle">{timing}</span>}
      </div>
      {present ? children : <div className="text-[11.5px] text-red-600 dark:text-red-400">layer not recorded</div>}
    </div>
  );
}

const ms = (a?: string | null, b?: string | null) => (a && b ? `${Math.max(0, new Date(b).getTime() - new Date(a).getTime())} ms` : undefined);

function StateDiffView({ before, after }: { before?: Record<string, unknown>; after?: Record<string, unknown> }) {
  const rows = stateDiff(before, after).filter((r) => r.kind !== "same");
  if (!before && !after) return <div className="text-[11px] italic text-subtle">no state snapshot</div>;
  if (!rows.length) return <div className="text-[11px] text-subtle">state unchanged</div>;
  return (
    <div className="space-y-0.5 font-mono text-[11px]">
      {rows.map((r) => (
        <div key={r.key} className="flex flex-wrap items-baseline gap-1">
          <span className={cn("font-semibold", r.kind === "added" && "text-emerald-600 dark:text-emerald-400", r.kind === "removed" && "text-red-600 dark:text-red-400", r.kind === "changed" && "text-amber-700 dark:text-amber-400")}>
            {r.kind === "added" ? "+" : r.kind === "removed" ? "−" : "~"} {r.key}
          </span>
          {r.kind !== "added" && <span className="text-muted line-through">{str(r.before)}</span>}
          {r.kind !== "removed" && <span className="break-all">→ {str(r.after)}</span>}
        </div>
      ))}
    </div>
  );
}

function Json({ v }: { v: unknown }) {
  return <pre className="max-h-40 overflow-auto rounded bg-panel-2 px-1.5 py-1 font-mono text-[10.5px] leading-snug thin-scroll">{JSON.stringify(v, null, 2)}</pre>;
}

/* --------------------------------------------------------------- turn card */

export function TurnCard({ t }: { t: TraceTurn }) {
  const L = t.layers ?? {};
  const caller = L.caller?.output;
  const ep = L.endpointing?.output;
  const stt = L.speech_to_text?.output;
  const llm = L.llm;
  const tools = L.tools;
  const tts = L.text_to_speech;
  const intended = caller?.intended_text ?? null;
  const final = stt?.final_hypothesis ?? null;
  const source = tts?.input?.text ?? null;
  const spoken = tts?.output?.spoken_text ?? null;
  const ttsMismatch = source != null && spoken != null && source !== spoken;
  const sttMismatch = intended != null && final != null && intended !== final;
  return (
    <Card className="p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-[13px] font-semibold">Turn {t.index}</span>
        {t.node_id && <span className="font-mono text-[11.5px] text-muted">{t.node_id}</span>}
        {sttMismatch && <Badge tone="amber">STT ≠ intended</Badge>}
        {ttsMismatch && <Badge tone="red">TTS ≠ source</Badge>}
        {!!t.missing?.length && <Badge tone="red">{t.missing.length} missing</Badge>}
      </div>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
        <LayerCard name="caller" present={!!L.caller} timing={ms(L.caller?.started_at, L.caller?.finished_at)}>
          <KV k="intended">{intended != null ? <Quote>{intended}</Quote> : <span className="italic text-subtle">unknown (no oracle truth)</span>}</KV>
          <ArtifactPlayer a={caller?.audio} label="caller audio" />
        </LayerCard>
        <LayerCard name="endpointing" present={!!L.endpointing} timing={ms(L.endpointing?.started_at, L.endpointing?.finished_at)}>
          <KV k="speech">
            <span className="font-mono">
              {ep?.speech_start_ms ?? "—"} → {ep?.speech_end_ms ?? "—"} ms
            </span>
          </KV>
          <KV k="interrupted">{ep?.interrupted_bot ? <Badge tone="amber">bot interrupted</Badge> : <span className="text-muted">no</span>}</KV>
          {ep?.segment_text != null && (
            <KV k="segment">
              <Quote>{intended ? <DiffText a={intended} b={ep.segment_text} mode="target" /> : ep.segment_text}</Quote>
              {intended && ep.segment_text.length < intended.length && <div className="mt-0.5 text-[10.5px] text-amber-700 dark:text-amber-400">cut off: “{intended.slice(ep.segment_text.length)}”</div>}
            </KV>
          )}
        </LayerCard>
        <LayerCard name="speech_to_text" present={!!L.speech_to_text} timing={ms(L.speech_to_text?.started_at, L.speech_to_text?.finished_at)}>
          {!!stt?.interim_hypotheses?.length && (
            <KV k="interim">
              <div className="flex flex-wrap gap-1">
                {stt.interim_hypotheses.map((h, i) => (
                  <span key={i} className="rounded bg-panel-2 px-1 font-mono text-[10.5px] text-muted">
                    {h}
                  </span>
                ))}
              </div>
            </KV>
          )}
          <KV k="final">
            {final != null ? <Quote tone="brand">{intended ? <DiffText a={intended} b={final} mode="target" /> : final}</Quote> : <span className="text-red-500">missing</span>}
          </KV>
          <KV k="confidence">
            <span className={cn("font-mono", (stt?.confidence ?? 1) < 0.85 && "text-amber-700 dark:text-amber-400")}>{stt?.confidence != null ? stt.confidence.toFixed(2) : "—"}</span>
          </KV>
          {L.speech_to_text?.settings && (
            <KV k="provider">
              <span className="font-mono text-[10.5px] text-muted">{[L.speech_to_text.settings.provider, L.speech_to_text.settings.model, L.speech_to_text.settings.language].filter(Boolean).join(" · ")}</span>
            </KV>
          )}
        </LayerCard>
        <LayerCard name="llm" present={!!llm} timing={ms(llm?.started_at, llm?.finished_at)}>
          <KV k="response">{llm?.output?.response_text ? <Quote>{llm.output.response_text}</Quote> : <span className="text-muted">—</span>}</KV>
          {(llm?.output?.tool_calls ?? []).map((c, i) => (
            <div key={i} className="rounded-md border border-violet-500/20 bg-violet-500/5 p-1.5">
              <div className="mb-0.5 font-mono text-[11px] font-semibold text-violet-700 dark:text-violet-300">{c.name}()</div>
              <div className="text-[10px] uppercase tracking-wider text-subtle">args</div>
              <Json v={c.arguments ?? {}} />
              {c.result !== undefined && (
                <>
                  <div className="text-[10px] uppercase tracking-wider text-subtle">result</div>
                  <Json v={c.result} />
                </>
              )}
            </div>
          ))}
          <KV k="model">
            <span className="font-mono text-[10.5px] text-muted">
              {[llm?.settings?.model, llm?.settings?.temperature != null && `t=${llm.settings.temperature}`, llm?.settings?.seed != null && `seed ${llm.settings.seed}`].filter(Boolean).join(" · ") || "—"}
            </span>
          </KV>
          {llm?.input?.context_hash && (
            <KV k="context">
              <span className="font-mono text-[10.5px] text-muted" title={llm.input.context_hash}>
                {shortSha(llm.input.context_hash)} · {llm.input.message_count ?? "?"} msgs
              </span>
            </KV>
          )}
        </LayerCard>
        <LayerCard name="tools" present={!!tools} timing={ms(tools?.started_at, tools?.finished_at)}>
          {(tools?.input?.calls ?? []).length === 0 && <div className="text-[11px] text-subtle">no tool calls this turn</div>}
          {(tools?.input?.calls ?? []).map((c, i) => {
            const r = tools?.output?.results?.[i];
            const ok = r ? r.ok !== false : undefined;
            return (
              <div key={i} className="rounded-md border border-cyan-500/20 bg-cyan-500/5 p-1.5">
                <div className="mb-0.5 flex items-center gap-1 font-mono text-[11px] font-semibold">
                  {ok === false ? <XCircle className="size-3 text-red-500" /> : <CheckCircle2 className="size-3 text-emerald-500" />}
                  {str(c.kind)} {str(c.dataset ?? c.url)}
                </div>
                <div className="text-[10px] uppercase tracking-wider text-subtle">input</div>
                <Json v={c.query ?? c} />
                {r && (
                  <>
                    <div className="text-[10px] uppercase tracking-wider text-subtle">result</div>
                    <Json v={r.rows ?? r.response ?? r.error ?? r} />
                  </>
                )}
              </div>
            );
          })}
          <div className="text-[10px] uppercase tracking-wider text-subtle">state before → after</div>
          <StateDiffView before={tools?.output?.state_before ?? t.state_before} after={tools?.output?.state_after ?? t.state_after} />
        </LayerCard>
        <LayerCard name="text_to_speech" present={!!tts} timing={ms(tts?.started_at, tts?.finished_at)}>
          <KV k="source">{source != null ? <Quote>{spoken != null ? <DiffText a={source} b={spoken} mode="source" /> : source}</Quote> : <span className="text-red-500">missing</span>}</KV>
          <KV k="spoken">
            {spoken != null ? <Quote tone={ttsMismatch ? undefined : "brand"}>{source != null ? <DiffText a={source} b={spoken} mode="target" /> : spoken}</Quote> : <span className="text-red-500">missing</span>}
          </KV>
          <ArtifactPlayer a={tts?.output?.audio} label="TTS audio" />
        </LayerCard>
      </div>
    </Card>
  );
}

/* ---------------------------------------------------------------- artifacts */

function collectArtifacts(trace: SessionTrace) {
  const out: { turn: number; layer: string; a: TraceArtifact }[] = [];
  const seen = new Set<string>();
  const add = (turn: number, layer: string, a?: TraceArtifact | null) => {
    if (!a || seen.has(a.uri)) return;
    seen.add(a.uri);
    out.push({ turn, layer, a });
  };
  for (const t of trace.turns) {
    add(t.index, "caller", t.layers.caller?.output?.audio);
    add(t.index, "stt", t.layers.speech_to_text?.input?.audio);
    add(t.index, "tts", t.layers.text_to_speech?.output?.audio);
  }
  return out;
}

/* -------------------------------------------------------------- bind oracle */

function BindOracle({ trace, botId, onBound }: { trace: SessionTrace; botId: string; onBound: () => void }) {
  const toast = useToast();
  const { data: oracles } = useSWR<TestOracle[]>(`oracles:${botId}`, () => api.oracles(botId), { shouldRetryOnError: false });
  const [pick, setPick] = useState("");
  const [busy, setBusy] = useState(false);
  const bound = oracles?.find((o) => o.id === trace.oracle_id);
  const bind = async () => {
    if (!pick) return;
    setBusy(true);
    try {
      await api.bindOracle(trace.session.id, pick);
      toast("Oracle bound to session", "success");
      onBound();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-1.5">
      <div className="text-[12px]">
        {trace.oracle_id ? (
          <span className="flex items-center gap-1">
            <CheckCircle2 className="size-3.5 text-emerald-500" /> {bound ? bound.name : <span className="font-mono">{trace.oracle_id}</span>}
          </span>
        ) : (
          <span className="text-muted">No test oracle bound.</span>
        )}
      </div>
      <div className="flex gap-1.5">
        <Select value={pick} onChange={(e) => setPick(e.target.value)} className="flex-1" aria-label="Oracle">
          <option value="">{oracles ? (oracles.length ? "Choose oracle…" : "No oracles for this bot") : "Loading…"}</option>
          {(oracles ?? []).map((o) => (
            <option key={o.id} value={o.id}>
              {o.name} ({o.slug})
            </option>
          ))}
        </Select>
        <Button size="md" variant="outline" onClick={bind} disabled={!pick || pick === trace.oracle_id} loading={busy}>
          <Link2 className="size-3.5" /> Bind
        </Button>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- main view */

export function TraceView({ sessionId, botId }: { sessionId: string; botId: string }) {
  const toast = useToast();
  const { data: trace, error, isLoading, mutate } = useSWR<SessionTrace>(`trace:${sessionId}`, () => api.trace(sessionId), { shouldRetryOnError: false });
  const [exporting, setExporting] = useState(false);

  const exportEvidence = async () => {
    setExporting(true);
    try {
      const ev = await api.artemisEvidence(sessionId);
      downloadJson(`artemis-evidence-${sessionId}.json`, ev);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setExporting(false);
    }
  };

  const actions = (
    <div className="flex flex-wrap gap-1.5">
      <Button size="sm" variant="outline" onClick={exportEvidence} loading={exporting}>
        <FileJson className="size-3.5" /> Export evidence
      </Button>
      <Button size="sm" variant="outline" disabled={!trace} onClick={() => trace && downloadJson(`trace-${sessionId}.json`, trace)}>
        <Download className="size-3.5" /> Download trace
      </Button>
      <a href={artemisImportUrl(sessionId)} target="_blank" rel="noreferrer" className="inline-flex h-7 items-center gap-1.5 rounded-md bg-brand px-2 text-xs font-medium text-brand-fg shadow-sm hover:opacity-90">
        <ExternalLink className="size-3.5" /> Open in Artemis
      </a>
    </div>
  );

  if (isLoading)
    return (
      <div className="flex items-center justify-center gap-2 py-16 text-muted">
        <Spinner /> Loading trace…
      </div>
    );
  if (error || !trace) {
    const notFound = error instanceof ApiError && (error.status === 404 || error.status === 405);
    return (
      <div className="space-y-3">
        {actions}
        {notFound ? (
          <Card>
            <EmptyState icon={<AudioLines className="size-7" />} title="No replay-grade trace">
              The backend has no trace for this session yet ({formatDetail(error.detail) || `HTTP ${error.status}`}). Evidence export may still work.
            </EmptyState>
          </Card>
        ) : (
          <ErrorBox error={error ?? "Trace unavailable"} onRetry={() => mutate()} />
        )}
      </div>
    );
  }

  const artifacts = collectArtifacts(trace);
  const faults = faultLayers(trace.fault_profile);

  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_360px]">
      <div className="min-w-0 space-y-3">
        {trace.turns.length === 0 ? (
          <Card>
            <EmptyState icon={<BotIcon className="size-7" />} title="No traced turns">
              This session has no per-turn trace records.
            </EmptyState>
          </Card>
        ) : (
          trace.turns.map((t) => <TurnCard key={t.index} t={t} />)
        )}
      </div>
      <div className="space-y-4">
        <Card className="p-4">
          <SectionTitle
            right={
              <Button size="xs" variant="ghost" onClick={() => mutate()} title="Reload trace">
                <RefreshCw className="size-3" />
              </Button>
            }
          >
            Replay readiness
          </SectionTitle>
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            {trace.replay_ready ? (
              <Badge tone="green">
                <CheckCircle2 className="size-3" /> replay-ready
              </Badge>
            ) : (
              <Badge tone="red">
                <AlertTriangle className="size-3" /> not replay-ready · {trace.missing.length} missing
              </Badge>
            )}
            <Badge tone={trace.source === "live" ? "blue" : trace.source === "replay" ? "violet" : "gray"}>{trace.source}</Badge>
            {trace.seed != null && <Badge>seed {trace.seed}</Badge>}
            {faults.map((f) => (
              <Badge key={f} tone="amber" title="Fault injected in this session">
                fault: {f}
              </Badge>
            ))}
          </div>
          {trace.missing.length > 0 && (
            <ul className="mb-2 max-h-48 space-y-0.5 overflow-y-auto rounded-md border border-red-500/20 bg-red-500/5 p-2 font-mono text-[11px] text-red-700 thin-scroll dark:text-red-300">
              {trace.missing.map((m, i) => (
                <li key={i}>• {m}</li>
              ))}
            </ul>
          )}
          <div className="space-y-0.5 text-[11px] text-muted">
            <div>
              schema <span className="font-mono">{trace.schema_version}</span>
            </div>
            {trace.session.config_hash && (
              <div title={trace.session.config_hash}>
                config <span className="font-mono">{shortSha(trace.session.config_hash)}</span>
              </div>
            )}
            {trace.end_node && (
              <div>
                end node <span className="font-mono">{trace.end_node}</span>
              </div>
            )}
          </div>
          <div className="mt-3">{actions}</div>
        </Card>
        <Card className="p-4">
          <SectionTitle>Test oracle</SectionTitle>
          <BindOracle trace={trace} botId={botId} onBound={() => mutate()} />
          <Link href={`/bots/${botId}?tab=oracles`} className="mt-2 inline-block text-[11.5px] text-brand hover:underline">
            Manage oracles →
          </Link>
        </Card>
        <ReplayPanel trace={trace} botId={botId} />
        <Card className="p-4">
          <SectionTitle>Artifacts ({artifacts.length})</SectionTitle>
          {artifacts.length === 0 ? (
            <div className="text-[12px] text-muted">No artifacts recorded.</div>
          ) : (
            <table className="w-full text-[11.5px]">
              <thead className="text-left text-[10px] uppercase tracking-wider text-muted">
                <tr>
                  <th className="pb-1 font-medium">File</th>
                  <th className="pb-1 font-medium">sha256</th>
                  <th className="pb-1 text-right font-medium">Size</th>
                </tr>
              </thead>
              <tbody>
                {artifacts.map(({ turn, layer, a }) => (
                  <tr key={a.uri} className="border-t border-border">
                    <td className="py-1 pr-2">
                      <a href={artifactUrl(a.uri)} target="_blank" rel="noreferrer" className="break-all font-mono text-brand hover:underline">
                        {fileOf(a.uri)}
                      </a>
                      <div className="text-[10px] text-subtle">
                        turn {turn} · {layer} · {a.media_type}
                      </div>
                    </td>
                    <td className="py-1 pr-2 align-top font-mono text-muted" title={a.sha256}>
                      <button
                        className="hover:text-fg"
                        onClick={() => {
                          navigator.clipboard?.writeText(a.sha256).then(() => toast("sha256 copied"), () => {});
                        }}
                      >
                        {a.sha256.slice(0, 10)}
                      </button>
                    </td>
                    <td className="py-1 text-right align-top tabular-nums">{fmtBytes(a.bytes)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
        {trace.fault_profile && faults.length > 0 && (
          <Card className="p-4">
            <SectionTitle>Fault profile</SectionTitle>
            <Json v={trace.fault_profile} />
          </Card>
        )}
      </div>
    </div>
  );
}
