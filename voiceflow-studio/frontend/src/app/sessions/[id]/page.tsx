"use client";

import { AlertTriangle, ArrowLeft, Bot as BotIcon, CheckCircle2, Database, Eye, EyeOff, Lock, Moon, User, Webhook, XCircle, Zap } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState, type ReactNode } from "react";
import useSWR from "swr";
import { Badge, Button, Card, ErrorBox, SectionTitle, Spinner, cn, statusTone } from "@/components/ui";
import { fmtDuration } from "@/components/workspace/sessions-tab";
import { api } from "@/lib/api";
import type { SessionDetail, SessionEvent } from "@/lib/types";

const str = (v: unknown) => (v == null ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v));
const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour12: false });

function EventRow({ e, start }: { e: SessionEvent; start: number }) {
  const p = e.payload ?? {};
  const rel = `+${Math.max(0, Math.round((new Date(e.ts).getTime() - start) / 1000))}s`;
  const text = (p.text as string) ?? "";
  if (e.type === "user_transcript" || e.type === "bot_transcript") {
    const user = e.type === "user_transcript";
    return (
      <div className={cn("flex gap-2", user && "flex-row-reverse")}>
        <span className={cn("mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full", user ? "bg-panel-2 text-muted" : "bg-brand/10 text-brand")}>
          {user ? <User className="size-3.5" /> : <BotIcon className="size-3.5" />}
        </span>
        <div className={cn("max-w-[75%] rounded-2xl px-3 py-1.5 text-[13px]", user ? "rounded-tr-sm bg-brand text-brand-fg" : "rounded-tl-sm bg-panel-2")}>{text || str(p)}</div>
        <span className="self-end text-[10px] text-subtle">{rel}</span>
      </div>
    );
  }
  if (e.type === "node_enter")
    return (
      <div className="flex items-center gap-2 py-1 text-[10.5px] uppercase tracking-wider text-subtle">
        <span className="h-px flex-1 bg-border" />
        {(p.label as string) ?? e.node_id} <span className="font-mono normal-case">({e.node_id})</span> · {rel}
        <span className="h-px flex-1 bg-border" />
      </div>
    );
  const meta: Record<string, { icon: ReactNode; tone: string }> = {
    field_captured: { icon: <CheckCircle2 className="size-3.5 text-emerald-500" />, tone: "" },
    validation_error: { icon: <XCircle className="size-3.5 text-red-500" />, tone: "text-red-600 dark:text-red-400" },
    function_call: { icon: <Zap className="size-3.5 text-violet-500" />, tone: "" },
    lookup: { icon: <Database className="size-3.5 text-cyan-500" />, tone: "" },
    api_call: { icon: <Webhook className="size-3.5 text-slate-500" />, tone: "" },
    error: { icon: <AlertTriangle className="size-3.5 text-red-500" />, tone: "text-red-600 dark:text-red-400" },
    idle: { icon: <Moon className="size-3.5 text-amber-500" />, tone: "" },
    end: { icon: <CheckCircle2 className="size-3.5 text-brand" />, tone: "font-medium" },
  };
  const m = meta[e.type] ?? { icon: <Zap className="size-3.5 text-muted" />, tone: "" };
  return (
    <div className="mx-8 flex items-start gap-2 rounded-md border border-border bg-panel px-2 py-1 text-[11.5px]">
      {m.icon}
      <span className={cn("font-mono font-semibold", m.tone)}>{e.type}</span>
      <span className="flex-1 break-all font-mono text-muted">{Object.keys(p).length ? JSON.stringify(p) : ""}</span>
      <span className="text-[10px] text-subtle">{rel}</span>
    </div>
  );
}

function Meta({ k, v }: { k: string; v: ReactNode }) {
  return (
    <div className="flex justify-between gap-3 border-b border-border py-1.5 text-[12px] last:border-0">
      <span className="text-muted">{k}</span>
      <span className="text-right">{v}</span>
    </div>
  );
}

export default function SessionPage() {
  const { id } = useParams<{ id: string }>();
  const [reveal, setReveal] = useState(false);
  const { data: s, error, mutate } = useSWR<SessionDetail>(`session:${id}:${reveal}`, () => api.session(id, reveal), { keepPreviousData: true });

  if (error)
    return (
      <div className="mx-auto w-full max-w-3xl p-8">
        <ErrorBox error={error} onRetry={() => mutate()} />
      </div>
    );
  if (!s)
    return (
      <div className="flex flex-1 items-center justify-center gap-2 text-muted">
        <Spinner /> Loading session…
      </div>
    );
  const start = new Date(s.started_at).getTime();
  const snap = s.config_snapshot ?? {};
  const hasSensitive = s.captured.some((c) => c.sensitive) || s.masked;

  return (
    <div className="flex-1 overflow-y-auto thin-scroll">
      <div className="mx-auto max-w-7xl p-6">
        <Link href={`/bots/${s.bot_id}?tab=sessions`} className="mb-3 inline-flex items-center gap-1 text-[12px] text-muted hover:text-fg">
          <ArrowLeft className="size-3.5" /> {s.bot_name ?? "Bot"} sessions
        </Link>
        <div className="mb-5 flex flex-wrap items-center gap-2">
          <h1 className="text-lg font-semibold">Session</h1>
          <span className="font-mono text-[12px] text-muted">{s.id}</span>
          <Badge tone={statusTone(s.status)}>{s.status}</Badge>
          <Badge tone={s.mode === "published" ? "green" : "gray"}>{s.mode}</Badge>
          {s.flow_version != null && <Badge>flow v{s.flow_version}</Badge>}
          {s.end_reason && <span className="font-mono text-[12px] text-muted">· {s.end_reason}</span>}
        </div>
        <div className="grid grid-cols-1 gap-5 lg:grid-cols-[1fr_380px]">
          <Card className="p-4">
            <SectionTitle>Timeline</SectionTitle>
            <div className="space-y-2">
              {s.events.length === 0 && <div className="py-8 text-center text-[12px] text-muted">No events recorded.</div>}
              {s.events.map((e) => (
                <EventRow key={e.id} e={e} start={start} />
              ))}
            </div>
          </Card>
          <div className="space-y-5">
            <Card className="p-4">
              <SectionTitle
                right={
                  hasSensitive && (
                    <Button size="xs" variant="ghost" onClick={() => setReveal(!reveal)}>
                      {reveal ? <EyeOff className="size-3" /> : <Eye className="size-3" />} {reveal ? "Mask" : "Reveal"} sensitive
                    </Button>
                  )
                }
              >
                Captured fields
              </SectionTitle>
              {s.captured.length === 0 ? (
                <div className="py-4 text-center text-[12px] text-muted">Nothing captured.</div>
              ) : (
                <table className="w-full text-[12px]">
                  <thead className="text-left text-[10.5px] uppercase tracking-wider text-muted">
                    <tr>
                      <th className="pb-1 font-medium">Field</th>
                      <th className="pb-1 font-medium">Value</th>
                      <th className="pb-1 text-right font-medium">Tries</th>
                    </tr>
                  </thead>
                  <tbody>
                    {s.captured.map((c) => (
                      <tr key={c.id} className="border-t border-border">
                        <td className="py-1.5 pr-2 align-top">
                          <div className="flex items-center gap-1 font-mono">
                            {c.valid ? <CheckCircle2 className="size-3 text-emerald-500" /> : <XCircle className="size-3 text-red-500" />}
                            {c.field_name}
                            {c.sensitive && <Lock className="size-3 text-muted" />}
                          </div>
                          {c.node_id && <div className="text-[10.5px] text-subtle">{c.node_id}</div>}
                        </td>
                        <td className="break-all py-1.5 pr-2 align-top">{str(c.value)}</td>
                        <td className="py-1.5 text-right align-top tabular-nums">{c.attempts}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </Card>
            <Card className="p-4">
              <SectionTitle>Metadata</SectionTitle>
              <Meta k="Started" v={new Date(s.started_at).toLocaleString()} />
              <Meta k="Ended" v={s.ended_at ? time(s.ended_at) : "—"} />
              <Meta k="Duration" v={fmtDuration(s.duration_secs)} />
              <Meta k="End reason" v={<span className="font-mono">{s.end_reason ?? "—"}</span>} />
              <Meta k="Last node" v={<span className="font-mono">{s.current_node ?? "—"}</span>} />
              <Meta k="STT" v={snap.stt ? `${snap.stt.provider} · ${snap.stt.model} · ${snap.stt.language}` : "—"} />
              <Meta k="LLM" v={snap.llm ? `${snap.llm.provider} · ${snap.llm.model} · t=${snap.llm.temperature}` : "—"} />
              <Meta k="TTS" v={snap.tts ? `${snap.tts.provider} · ${snap.tts.voice} · ${snap.tts.speed}×` : "—"} />
            </Card>
            {s.variables && Object.keys(s.variables).length > 0 && (
              <Card className="p-4">
                <SectionTitle>All variables</SectionTitle>
                {Object.entries(s.variables).map(([k, v]) => (
                  <Meta key={k} k={k} v={<span className="break-all font-mono text-[11.5px]">{str(v)}</span>} />
                ))}
              </Card>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
