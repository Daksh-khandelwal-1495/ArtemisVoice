"use client";

import { ArrowDown, ArrowUp, CheckCircle2, ExternalLink, FlaskConical, Play, Plus, Save, Trash2, X, XCircle } from "lucide-react";
import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";
import useSWR from "swr";
import { OutcomeDiff } from "@/components/session/replay-panel";
import { Badge, Button, Card, EmptyState, ErrorBox, Field, IconButton, Input, SectionTitle, Segmented, Select, Spinner, Switch, cn, useConfirm, useToast } from "@/components/ui";
import { ChipsInput, KVEditor } from "@/components/ui/editors";
import { ApiError, api, artemisImportUrl } from "@/lib/api";
import { SESSION_STATUSES, cleanFaultProfile, compareOutcome, faultLayers } from "@/lib/oracle";
import type { BotDetail, Dataset, FaultProfile, FlowGraph, OracleBody, SessionStatus, SimulateResponse, TestOracle } from "@/lib/types";
import { FaultProfileEditor } from "./fault-profile-editor";

/** Like <Field> but a <div>, for groups holding several buttons (a <label> would forward stray clicks). */
function Group({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <div className="text-[11px] font-medium uppercase tracking-wide text-muted">{label}</div>
      {children}
      {hint && <div className="text-[11px] text-muted">{hint}</div>}
    </div>
  );
}

const QUICK_TAGS = ["regression", "held-out", "demo"];

const blankOracle = (): OracleBody => ({
  name: "New oracle",
  slug: "",
  description: "",
  caller_turns: [""],
  expected_fields: {},
  expected_lookup: null,
  expected_end_node: null,
  expected_status: "completed",
  expected_bot_response: null,
  tags: [],
});

const toBody = (o: TestOracle): OracleBody => ({
  name: o.name,
  slug: o.slug,
  description: o.description ?? "",
  caller_turns: [...(o.caller_turns ?? [])],
  expected_fields: { ...(o.expected_fields ?? {}) },
  expected_lookup: o.expected_lookup ? { dataset: o.expected_lookup.dataset, match: { ...o.expected_lookup.match } } : null,
  expected_end_node: o.expected_end_node ?? null,
  expected_status: o.expected_status ?? null,
  expected_bot_response: o.expected_bot_response ? { contains: [...(o.expected_bot_response.contains ?? [])] } : null,
  tags: [...(o.tags ?? [])],
});

function CallerTurns({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  const move = (i: number, d: number) => {
    const j = i + d;
    if (j < 0 || j >= value.length) return;
    const next = [...value];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  return (
    <div className="space-y-1">
      {value.map((t, i) => (
        <div key={i} className="flex items-center gap-1">
          <span className="w-5 text-right font-mono text-[10.5px] text-subtle">{i}</span>
          <Input value={t} placeholder="What the caller truly said" onChange={(e) => onChange(value.map((x, j) => (j === i ? e.target.value : x)))} />
          <IconButton title="Move up" onClick={() => move(i, -1)} disabled={i === 0}>
            <ArrowUp className="size-3.5" />
          </IconButton>
          <IconButton title="Move down" onClick={() => move(i, 1)} disabled={i === value.length - 1}>
            <ArrowDown className="size-3.5" />
          </IconButton>
          <IconButton title="Remove turn" onClick={() => onChange(value.filter((_, j) => j !== i))}>
            <X className="size-3.5" />
          </IconButton>
        </div>
      ))}
      <Button size="sm" variant="ghost" onClick={() => onChange([...value, ""])}>
        <Plus className="size-3.5" /> Add caller turn
      </Button>
    </div>
  );
}

function OracleEditor({ draft, setDraft, graph, datasets }: { draft: OracleBody; setDraft: (d: OracleBody) => void; graph: FlowGraph; datasets: Dataset[] }) {
  const set = <K extends keyof OracleBody>(k: K, v: OracleBody[K]) => setDraft({ ...draft, [k]: v });
  const fields = useMemo(() => [...new Set(graph.nodes.flatMap((n) => (n.fields ?? []).map((f) => f.name)))], [graph]);
  const terminal = graph.nodes.filter((n) => n.type === "end" || n.type === "transfer");
  const others = graph.nodes.filter((n) => n.type !== "end" && n.type !== "transfer");
  const lk = draft.expected_lookup;
  const lkCols = datasets.find((d) => d.name === lk?.dataset)?.columns;
  const tags = draft.tags ?? [];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Name">
          <Input value={draft.name ?? ""} onChange={(e) => set("name", e.target.value)} />
        </Field>
        <Field label="Slug" hint="Blank = derived from name">
          <Input value={draft.slug ?? ""} className="font-mono" placeholder="tata-happy" onChange={(e) => set("slug", e.target.value)} />
        </Field>
      </div>
      <Field label="Description">
        <Input value={draft.description ?? ""} placeholder="What this journey proves" onChange={(e) => set("description", e.target.value)} />
      </Field>
      <Group label="Tags">
        <div className="space-y-1">
          <div className="flex flex-wrap gap-1">
            {QUICK_TAGS.map((t) => {
              const on = tags.includes(t);
              return (
                <button
                  key={t}
                  type="button"
                  onClick={() => set("tags", on ? tags.filter((x) => x !== t) : [...tags, t])}
                  className={cn("rounded-full border px-2 py-0.5 text-[11px] font-medium", on ? "border-brand/40 bg-brand/10 text-brand" : "border-border text-muted hover:text-fg")}
                >
                  {on ? "✓ " : "+ "}
                  {t}
                </button>
              );
            })}
          </div>
          <ChipsInput value={tags} onChange={(v) => set("tags", v)} placeholder="Add tag…" />
        </div>
      </Group>
      <div>
        <SectionTitle>Caller turns (ground truth, in order)</SectionTitle>
        <CallerTurns value={draft.caller_turns ?? []} onChange={(v) => set("caller_turns", v)} />
      </div>
      <div>
        <SectionTitle>Expected captured fields</SectionTitle>
        <KVEditor value={draft.expected_fields ?? {}} onChange={(v) => set("expected_fields", v)} keyPlaceholder="field" valuePlaceholder="expected value" keyOptions={fields} />
      </div>
      <div>
        <SectionTitle right={<Switch checked={!!lk} onChange={(v) => set("expected_lookup", v ? { dataset: datasets[0]?.name ?? "", match: {} } : null)} label="Expect lookup" />}>Expected lookup row</SectionTitle>
        {lk ? (
          <div className="space-y-2">
            <Field label="Dataset">
              <Select value={lk.dataset} onChange={(e) => set("expected_lookup", { ...lk, dataset: e.target.value })}>
                {!datasets.some((d) => d.name === lk.dataset) && <option value={lk.dataset}>{lk.dataset || "Choose dataset…"}</option>}
                {datasets.map((d) => (
                  <option key={d.id} value={d.name}>
                    {d.name}
                  </option>
                ))}
              </Select>
            </Field>
            <Group label="Match (column → value)">
              <KVEditor value={lk.match} onChange={(m) => set("expected_lookup", { ...lk, match: m })} keyPlaceholder="column" valuePlaceholder="value" keyOptions={lkCols} />
            </Group>
          </div>
        ) : (
          <div className="text-[12px] text-muted">No lookup expectation.</div>
        )}
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Expected end node">
          <Select value={draft.expected_end_node ?? ""} onChange={(e) => set("expected_end_node", e.target.value || null)}>
            <option value="">(any)</option>
            <optgroup label="Terminal">
              {terminal.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.label} ({n.id})
                </option>
              ))}
            </optgroup>
            <optgroup label="Other">
              {others.map((n) => (
                <option key={n.id} value={n.id}>
                  {n.label} ({n.id})
                </option>
              ))}
            </optgroup>
          </Select>
        </Field>
        <Field label="Expected status">
          <Select value={draft.expected_status ?? ""} onChange={(e) => set("expected_status", (e.target.value || null) as SessionStatus | null)}>
            <option value="">(any)</option>
            {SESSION_STATUSES.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </Select>
        </Field>
      </div>
      <Group label="Bot response must contain" hint="Checked against the final spoken confirmation (TTS output).">
        <ChipsInput
          value={draft.expected_bot_response?.contains ?? []}
          onChange={(v) => set("expected_bot_response", v.length ? { contains: v } : null)}
          placeholder="e.g. Nexon, 411001"
        />
      </Group>
    </div>
  );
}

function SimResult({ r, oracle, faults }: { r: SimulateResponse; oracle: TestOracle | undefined; faults: string[] }) {
  return (
    <div className={cn("rounded-md border p-3", r.passed ? "border-emerald-500/30 bg-emerald-500/5" : "border-red-500/30 bg-red-500/5")}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        {r.passed ? <CheckCircle2 className="size-4 text-emerald-500" /> : <XCircle className="size-4 text-red-500" />}
        <span className={cn("text-[13px] font-semibold", r.passed ? "text-emerald-700 dark:text-emerald-400" : "text-red-700 dark:text-red-400")}>{r.passed ? "Passed" : "Failed"}</span>
        {oracle && <span className="text-[12px] text-muted">{oracle.name}</span>}
        {faults.map((f) => (
          <Badge key={f} tone="amber">
            fault: {f}
          </Badge>
        ))}
      </div>
      <OutcomeDiff rows={compareOutcome(oracle, r.outcome)} />
      <div className="mt-2 flex flex-wrap items-center gap-3 text-[12px]">
        <Link href={`/sessions/${r.session_id}`} className="text-brand hover:underline">
          Session →
        </Link>
        <Link href={`/sessions/${r.session_id}?tab=trace`} className="text-brand hover:underline">
          Trace →
        </Link>
        <a href={artemisImportUrl(r.session_id)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-brand hover:underline">
          <ExternalLink className="size-3" /> Open in Artemis
        </a>
        <span className="ml-auto font-mono text-[10.5px] text-subtle">{r.session_id.slice(0, 8)}</span>
      </div>
    </div>
  );
}

function SimulatePanel({ bot, oracle, fault }: { bot: BotDetail; oracle: TestOracle | undefined; fault: FaultProfile }) {
  const toast = useToast();
  const [mode, setMode] = useState<"draft" | "published">("draft");
  const [seed, setSeed] = useState("7");
  const [busy, setBusy] = useState(false);
  const [results, setResults] = useState<{ r: SimulateResponse; oracle: TestOracle | undefined; faults: string[] }[]>([]);
  const run = async () => {
    if (!oracle) return;
    setBusy(true);
    const fp = cleanFaultProfile(fault);
    try {
      const r = await api.simulate(bot.id, { oracle_id: oracle.id, fault_profile: fp, mode, ...(seed !== "" ? { seed: Number(seed) } : {}) });
      setResults((x) => [{ r, oracle, faults: faultLayers(fp) }, ...x].slice(0, 6));
    } catch (e) {
      toast(e instanceof ApiError && e.status === 404 ? `Simulate unavailable: ${e.message}` : (e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card className="p-4">
      <SectionTitle>Simulate</SectionTitle>
      <div className="flex flex-wrap items-end gap-2">
        <Group label="Flow">
          <Segmented
            value={mode}
            onChange={setMode}
            items={[
              { value: "draft", label: "Draft" },
              { value: "published", label: "Published", disabled: bot.published_version == null },
            ]}
          />
        </Group>
        <Field label="Seed">
          <Input type="number" value={seed} onChange={(e) => setSeed(e.target.value)} className="w-20" />
        </Field>
        <Button variant="primary" onClick={run} loading={busy} disabled={!oracle} className="ml-auto" title={oracle ? "Script the caller from the oracle and run the real flow" : "Save the oracle first"}>
          <Play className="size-3.5" /> Simulate
        </Button>
      </div>
      <div className="mt-1 text-[11px] text-muted">Deterministic text driver: caller scripted from the oracle’s turns, fault profile applied, sandboxed (API_CALL_MOCK).</div>
      {results.length > 0 && (
        <div className="mt-3 space-y-2">
          {results.map((x) => (
            <SimResult key={x.r.session_id} {...x} />
          ))}
        </div>
      )}
    </Card>
  );
}

export function OraclesTab({ bot, graph }: { bot: BotDetail; graph: FlowGraph }) {
  const toast = useToast();
  const confirm = useConfirm();
  const { data: oracles, error, isLoading, mutate } = useSWR<TestOracle[]>(`oracles:${bot.id}`, () => api.oracles(bot.id), { shouldRetryOnError: false });
  const { data: datasets } = useSWR<Dataset[]>("datasets", api.datasets);
  const [selId, setSelId] = useState<string | "new" | null>(null);
  const [draft, setDraft] = useState<OracleBody | null>(null);
  const [fault, setFault] = useState<FaultProfile>({});
  const [busy, setBusy] = useState<"save" | "delete" | "fault" | null>(null);

  const sel = oracles?.find((o) => o.id === selId);
  const select = (o: TestOracle | "new") => {
    if (o === "new") {
      setSelId("new");
      setDraft(blankOracle());
      setFault({});
    } else {
      setSelId(o.id);
      setDraft(toBody(o));
      setFault(structuredClone(o.fault_profile ?? {}));
    }
  };
  // Auto-select first oracle once loaded (derived-state update during render).
  if (selId === null && oracles && oracles.length) select(oracles[0]);

  const dirty = !!draft && (selId === "new" || (sel && JSON.stringify(toBody(sel)) !== JSON.stringify(draft)));
  const fields = useMemo(() => [...new Set(graph.nodes.flatMap((n) => (n.fields ?? []).map((f) => f.name)))], [graph]);

  const save = async () => {
    if (!draft) return;
    setBusy("save");
    const body: OracleBody = { ...draft, caller_turns: (draft.caller_turns ?? []).map((t) => t.trim()).filter(Boolean), slug: draft.slug?.trim() || undefined };
    try {
      const o = selId === "new" || !sel ? await api.createOracle(bot.id, body) : await api.patchOracle(sel.id, body);
      await mutate();
      setSelId(o.id);
      setDraft(toBody(o));
      toast(`Saved oracle ${o.slug}`, "success");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };

  const faultSaved = !!sel && JSON.stringify(cleanFaultProfile(sel.fault_profile)) === JSON.stringify(cleanFaultProfile(fault));
  const saveFault = async () => {
    if (!sel) return;
    setBusy("fault");
    try {
      await api.patchOracle(sel.id, { fault_profile: cleanFaultProfile(fault) });
      await mutate();
      toast("Fault profile saved on oracle", "success");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    if (!sel) return;
    if (!(await confirm({ title: `Delete oracle “${sel.name}”?`, message: "Sessions bound to it lose their expected outcome.", confirmLabel: "Delete", danger: true }))) return;
    setBusy("delete");
    try {
      await api.deleteOracle(sel.id);
      setSelId(null);
      setDraft(null);
      await mutate();
      toast("Oracle deleted", "success");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };

  const keywords = bot.config?.stt?.keywords ?? [];

  return (
    <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-[300px_1fr]">
      <div className="overflow-y-auto border-r border-border p-3 thin-scroll">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-muted">Test oracles</h3>
          <Button size="xs" variant="outline" onClick={() => select("new")}>
            <Plus className="size-3" /> New
          </Button>
        </div>
        {error && <ErrorBox error={error} onRetry={() => mutate()} />}
        {isLoading && (
          <div className="flex items-center gap-2 py-6 text-[12px] text-muted">
            <Spinner /> Loading…
          </div>
        )}
        {oracles && oracles.length === 0 && selId !== "new" && (
          <EmptyState icon={<FlaskConical className="size-6" />} title="No oracles yet">
            An oracle is the expected outcome of a scripted call. Create one to simulate and replay.
          </EmptyState>
        )}
        <div className="space-y-1">
          {selId === "new" && <div className="rounded-md border border-brand/40 bg-brand/5 px-2 py-1.5 text-[12.5px] font-medium">{draft?.name || "New oracle"} · unsaved</div>}
          {(oracles ?? []).map((o) => {
            const faults = faultLayers(o.fault_profile);
            return (
              <button
                key={o.id}
                onClick={() => select(o)}
                className={cn("w-full rounded-md border px-2 py-1.5 text-left", o.id === selId ? "border-brand/40 bg-brand/5" : "border-transparent hover:bg-panel-2")}
              >
                <div className="truncate text-[12.5px] font-medium">{o.name}</div>
                <div className="flex flex-wrap items-center gap-1 pt-0.5">
                  <span className="font-mono text-[10.5px] text-muted">{o.slug}</span>
                  {(o.tags ?? []).map((t) => (
                    <Badge key={t} tone={t === "regression" ? "blue" : t === "held-out" ? "violet" : "gray"}>
                      {t}
                    </Badge>
                  ))}
                  {faults.map((f) => (
                    <Badge key={f} tone="amber">
                      {f}
                    </Badge>
                  ))}
                </div>
              </button>
            );
          })}
        </div>
      </div>
      <div className="overflow-y-auto p-5 thin-scroll">
        {!draft ? (
          <EmptyState icon={<FlaskConical className="size-7" />} title="Select or create an oracle" />
        ) : (
          <div className="mx-auto grid max-w-6xl grid-cols-1 gap-5 xl:grid-cols-[1fr_420px]">
            <Card className="p-4">
              <div className="mb-3 flex items-center gap-2">
                <h2 className="text-[14px] font-semibold">{selId === "new" ? "New oracle" : "Edit oracle"}</h2>
                {dirty && <span className="text-[11.5px] text-amber-600 dark:text-amber-400">unsaved</span>}
                <div className="ml-auto flex gap-1.5">
                  {sel && (
                    <Button size="sm" variant="ghost" onClick={remove} loading={busy === "delete"}>
                      <Trash2 className="size-3.5" /> Delete
                    </Button>
                  )}
                  <Button size="sm" variant="primary" onClick={save} loading={busy === "save"} disabled={!dirty}>
                    <Save className="size-3.5" /> Save
                  </Button>
                </div>
              </div>
              <OracleEditor draft={draft} setDraft={setDraft} graph={graph} datasets={datasets ?? []} />
            </Card>
            <div className="space-y-5">
              <Card className="p-4">
                <SectionTitle
                  right={
                    sel && (
                      <Button size="xs" variant="ghost" onClick={saveFault} loading={busy === "fault"} disabled={faultSaved} title="Store this fault profile on the oracle (demo variant)">
                        <Save className="size-3" /> {faultSaved ? "Saved on oracle" : "Save to oracle"}
                      </Button>
                    )
                  }
                >
                  Fault profile (demo variant)
                </SectionTitle>
                <FaultProfileEditor value={fault} onChange={setFault} fields={fields} datasets={datasets ?? []} keywords={keywords} />
              </Card>
              <SimulatePanel bot={bot} oracle={selId === "new" ? undefined : sel} fault={fault} />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
