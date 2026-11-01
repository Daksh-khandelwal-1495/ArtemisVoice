"use client";

import { CheckCircle2, Play, XCircle } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { Badge, Button, Card, Field, Input, SectionTitle, Select, cn, useToast } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { INTERVENTIONS, compareOutcome, type OutcomeCheck } from "@/lib/oracle";
import type { Intervention, ReplayResponse, SessionTrace, TestOracle } from "@/lib/types";

export function OutcomeDiff({ rows }: { rows: OutcomeCheck[] }) {
  if (!rows.length) return <div className="text-[11px] text-subtle">No oracle expectations to compare.</div>;
  return (
    <table className="w-full text-[11.5px]">
      <thead className="text-left text-[10px] uppercase tracking-wider text-muted">
        <tr>
          <th className="pb-1 font-medium" />
          <th className="pb-1 font-medium">Check</th>
          <th className="pb-1 font-medium">Expected</th>
          <th className="pb-1 font-medium">Captured</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key} className="border-t border-border align-top">
            <td className="py-1 pr-1">{r.ok ? <CheckCircle2 className="size-3.5 text-emerald-500" /> : <XCircle className="size-3.5 text-red-500" />}</td>
            <td className="py-1 pr-2 font-mono">{r.key}</td>
            <td className="break-all py-1 pr-2">{r.expected}</td>
            <td className={cn("break-all py-1", !r.ok && "font-medium text-red-600 dark:text-red-400")}>{r.actual}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function ReplayPanel({ trace, botId }: { trace: SessionTrace; botId: string }) {
  const toast = useToast();
  const { data: oracles } = useSWR<TestOracle[]>(`oracles:${botId}`, () => api.oracles(botId), { shouldRetryOnError: false });
  const oracle = oracles?.find((o) => o.id === trace.oracle_id) ?? null;
  const [iv, setIv] = useState<Intervention>("control");
  const [trials, setTrials] = useState(3);
  const [seed, setSeed] = useState<string>(trace.seed != null ? String(trace.seed) : "");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<ReplayResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<number | null>(null);

  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.replay(trace.session.id, { intervention: iv, trials, ...(seed !== "" ? { seed: Number(seed) } : {}) });
      setRes(r);
      setOpen(r.trials.find((t) => !t.passed)?.trial ?? null);
    } catch (e) {
      const missing = e instanceof ApiError && typeof e.detail === "object" && e.detail && "missing" in e.detail ? (e.detail as { missing: string[] }).missing : null;
      setErr(missing ? `${(e as Error).message}: ${missing.slice(0, 3).join("; ")}${missing.length > 3 ? "…" : ""}` : (e as Error).message);
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const passed = res?.trials.filter((t) => t.passed).length ?? 0;
  const hint = INTERVENTIONS.find((x) => x.value === iv)?.hint;

  return (
    <Card className="p-4">
      <SectionTitle>Replay</SectionTitle>
      {!trace.replay_ready && <div className="mb-2 text-[11.5px] text-amber-700 dark:text-amber-400">Session is not replay-ready; the backend will refuse until every layer is captured.</div>}
      {!trace.oracle_id && <div className="mb-2 text-[11.5px] text-amber-700 dark:text-amber-400">Bind a test oracle first so each trial can be scored.</div>}
      <div className="grid grid-cols-[1fr_64px_64px] gap-2">
        <Field label="Intervention">
          <Select value={iv} onChange={(e) => setIv(e.target.value as Intervention)}>
            {INTERVENTIONS.map((x) => (
              <option key={x.value} value={x.value}>
                {x.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Trials">
          <Input type="number" min={1} max={10} value={trials} onChange={(e) => setTrials(Math.min(10, Math.max(1, Number(e.target.value) || 1)))} />
        </Field>
        <Field label="Seed">
          <Input type="number" value={seed} placeholder="7" onChange={(e) => setSeed(e.target.value)} />
        </Field>
      </div>
      {hint && <div className="mt-1 text-[11px] text-muted">{hint}</div>}
      <Button className="mt-2 w-full" variant="primary" size="sm" onClick={run} loading={busy}>
        <Play className="size-3.5" /> Replay {trials}×
      </Button>
      {err && <div className="mt-2 rounded-md border border-red-500/30 bg-red-500/5 px-2 py-1 text-[11.5px] text-red-600 dark:text-red-400">{err}</div>}
      {res && (
        <div className="mt-3 space-y-1.5">
          <div className="flex items-center gap-2 text-[12px]">
            <Badge tone={passed === res.trials.length ? "green" : passed === 0 ? "red" : "amber"}>
              {passed}/{res.trials.length} passed
            </Badge>
            <span className="font-mono text-muted">{res.intervention}</span>
            {res.seed != null && <span className="text-muted">seed {res.seed}</span>}
          </div>
          {res.trials.map((t) => (
            <div key={t.trial} className="rounded-md border border-border">
              <button className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-[12px] hover:bg-panel-2" onClick={() => setOpen(open === t.trial ? null : t.trial)}>
                {t.passed ? <CheckCircle2 className="size-3.5 text-emerald-500" /> : <XCircle className="size-3.5 text-red-500" />}
                <span className="font-medium">Trial {t.trial}</span>
                <span className="font-mono text-[11px] text-muted">
                  {t.outcome.end_node ?? "—"} · {t.outcome.status ?? "—"}
                </span>
                {t.replay_session_id && (
                  <Link href={`/sessions/${t.replay_session_id}?tab=trace`} onClick={(e) => e.stopPropagation()} className="ml-auto text-[11px] text-brand hover:underline">
                    session →
                  </Link>
                )}
              </button>
              {open === t.trial && (
                <div className="border-t border-border p-2">
                  {oracle ? (
                    <OutcomeDiff rows={compareOutcome(oracle, t.outcome)} />
                  ) : (
                    <pre className="overflow-auto font-mono text-[10.5px]">{JSON.stringify(t.outcome.captured, null, 2)}</pre>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}
