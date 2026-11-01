"use client";

import { ChevronDown, ChevronRight, Flag, MousePointerClick, Trash2 } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Badge, Button, Field, IconButton, Input, Select, Switch, Textarea } from "@/components/ui";
import { JsonEditor, KVEditor } from "@/components/ui/editors";
import { NODE_META, renameNode, updateNode } from "@/lib/flow";
import { availableVars } from "@/lib/lint";
import { SLUG_RE, type Dataset, type FlowGraph, type FlowIssue, type FlowNode, type NodeSettings } from "@/lib/types";
import type { GraphUpdater } from "@/lib/use-graph-history";
import { FieldsEditor } from "./fields-editor";
import { PromptEditor } from "./prompt-editor";
import { TransitionForm, TransitionsEditor } from "./transitions-editor";

function Section({ title, children, defaultOpen = true, right }: { title: string; children: ReactNode; defaultOpen?: boolean; right?: ReactNode }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <section className="border-b border-border">
      <div className="flex items-center justify-between px-3 py-2">
        <button type="button" onClick={() => setOpen(!open)} className="flex items-center gap-1 text-[11px] font-semibold uppercase tracking-wider text-muted hover:text-fg">
          {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          {title}
        </button>
        {right}
      </div>
      {open && <div className="space-y-2.5 px-3 pb-3">{children}</div>}
    </section>
  );
}

function IdEditor({ node, graph, onRename }: { node: FlowNode; graph: FlowGraph; onRename: (to: string) => void }) {
  const [v, setV] = useState(node.id);
  const [last, setLast] = useState(node.id);
  if (node.id !== last) {
    setLast(node.id);
    setV(node.id);
  }
  const taken = v !== node.id && graph.nodes.some((n) => n.id === v);
  const err = !SLUG_RE.test(v) ? "snake_case: a-z, 0-9, _ ; starts with a letter" : taken ? "Another node already uses this id" : null;
  const commit = () => {
    if (!err && v !== node.id) onRename(v);
    else if (err) setV(node.id);
  };
  return (
    <Field label="Node id" error={err} hint={v !== node.id && !err ? "Press Enter to rename — references are updated" : undefined}>
      <Input value={v} invalid={!!err} onChange={(e) => setV(e.target.value)} onBlur={commit} onKeyDown={(e) => e.key === "Enter" && commit()} className="font-mono text-[12px]" />
    </Field>
  );
}

function SettingsForm({ node, nodes, onChange }: { node: FlowNode; nodes: FlowNode[]; onChange: (s: NodeSettings) => void }) {
  const s = node.settings;
  return (
    <>
      {node.type !== "decision" && (
        <Field inline label="Respond immediately" hint="Bot speaks first on entering this node">
          <Switch checked={s.respond_immediately} onChange={(c) => onChange({ ...s, respond_immediately: c })} />
        </Field>
      )}
      <Field inline label="Allow interruptions">
        <Switch checked={s.allow_interruptions} onChange={(c) => onChange({ ...s, allow_interruptions: c })} />
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Context strategy">
          <Select value={s.context_strategy} onChange={(e) => onChange({ ...s, context_strategy: e.target.value as NodeSettings["context_strategy"] })}>
            <option value="append">append</option>
            <option value="reset">reset</option>
            <option value="reset_with_summary">reset_with_summary</option>
          </Select>
        </Field>
        <Field label="Silence timeout (s)" error={s.silence_timeout_secs != null && (s.silence_timeout_secs < 3 || s.silence_timeout_secs > 120) && "3–120"}>
          <Input
            type="number"
            min={3}
            max={120}
            placeholder="global"
            value={s.silence_timeout_secs ?? ""}
            onChange={(e) => onChange({ ...s, silence_timeout_secs: e.target.value === "" ? null : Number(e.target.value) })}
          />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Max attempts">
          <Input type="number" min={1} max={10} value={s.max_attempts} onChange={(e) => onChange({ ...s, max_attempts: Number(e.target.value) })} />
        </Field>
        <Field label="On failure">
          <Select value={s.on_failure} onChange={(e) => onChange({ ...s, on_failure: e.target.value as NodeSettings["on_failure"] })}>
            <option value="transfer">transfer</option>
            <option value="end">end</option>
            <option value="skip">skip</option>
            <option value="goto">goto…</option>
          </Select>
        </Field>
      </div>
      {s.on_failure === "goto" && (
        <Field label="Failure target" error={!nodes.some((n) => n.id === s.on_failure_target) && "Select a node"}>
          <Select value={s.on_failure_target ?? ""} onChange={(e) => onChange({ ...s, on_failure_target: e.target.value || null })}>
            <option value="">— select —</option>
            {nodes
              .filter((n) => n.id !== node.id)
              .map((n) => (
                <option key={n.id} value={n.id}>
                  {n.label} ({n.id})
                </option>
              ))}
          </Select>
        </Field>
      )}
    </>
  );
}

function LookupForm({ node, datasets, vars, onChange }: { node: FlowNode; datasets: Dataset[]; vars: string[]; onChange: (n: FlowNode) => void }) {
  const l = node.lookup ?? { dataset: "", match: {}, match_mode: "exact" as const, save_as: "result", single: true, limit: 5 };
  const set = (p: Partial<typeof l>) => onChange({ ...node, lookup: { ...l, ...p } });
  const ds = datasets.find((d) => d.name === l.dataset);
  return (
    <>
      <Field label="Dataset" error={!l.dataset ? "Required" : datasets.length && !ds ? "Unknown dataset" : null}>
        <Select value={l.dataset} onChange={(e) => set({ dataset: e.target.value })}>
          <option value="">— select dataset —</option>
          {datasets.map((d) => (
            <option key={d.id} value={d.name}>
              {d.name} ({d.row_count} rows)
            </option>
          ))}
          {l.dataset && !ds && <option value={l.dataset}>{l.dataset} (missing)</option>}
        </Select>
      </Field>
      {ds && <div className="flex flex-wrap gap-1">{ds.columns.map((c) => <Badge key={c}>{c}</Badge>)}</div>}
      <Field label="Match: dataset column → captured var">
        <KVEditor value={l.match} onChange={(m) => set({ match: m })} keyPlaceholder="column" valuePlaceholder="var" keyOptions={ds?.columns} valueOptions={vars} />
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Match mode">
          <Select value={l.match_mode} onChange={(e) => set({ match_mode: e.target.value as "exact" | "icontains" })}>
            <option value="exact">exact</option>
            <option value="icontains">icontains</option>
          </Select>
        </Field>
        <Field label="Save as" hint={`also sets ${l.save_as || "x"}_found`} error={!SLUG_RE.test(l.save_as) && "snake_case"}>
          <Input value={l.save_as} onChange={(e) => set({ save_as: e.target.value })} className="font-mono text-[12px]" />
        </Field>
      </div>
      <div className="grid grid-cols-2 items-end gap-2">
        <Field inline label="Single row">
          <Switch checked={l.single} onChange={(c) => set({ single: c })} />
        </Field>
        <Field label="Limit">
          <Input type="number" min={1} max={50} value={l.limit} onChange={(e) => set({ limit: Number(e.target.value) })} />
        </Field>
      </div>
    </>
  );
}

function ApiForm({ node, onChange }: { node: FlowNode; onChange: (n: FlowNode) => void }) {
  const a = node.api_call ?? { method: "POST" as const, url: "", headers: {}, body_template: null, timeout_secs: 8, save_as: "api_result", mock_response: null };
  const set = (p: Partial<typeof a>) => onChange({ ...node, api_call: { ...a, ...p } });
  return (
    <>
      <div className="grid grid-cols-[90px_1fr] gap-2">
        <Field label="Method">
          <Select value={a.method} onChange={(e) => set({ method: e.target.value as typeof a.method })}>
            {["GET", "POST", "PUT", "PATCH"].map((m) => (
              <option key={m}>{m}</option>
            ))}
          </Select>
        </Field>
        <Field label="URL" error={!a.url && !a.mock_response && "URL or mock response required"}>
          <Input value={a.url} onChange={(e) => set({ url: e.target.value })} placeholder="https://api.example.com/{{phone}}" className="font-mono text-[12px]" />
        </Field>
      </div>
      <Field label="Headers">
        <KVEditor value={a.headers} onChange={(h) => set({ headers: h })} keyPlaceholder="Header" valuePlaceholder="value" />
      </Field>
      {a.method !== "GET" && (
        <Field label="Body template (JSON, supports {{var}})">
          <JsonEditor value={a.body_template} onChange={(b) => set({ body_template: b })} rows={5} />
        </Field>
      )}
      <div className="grid grid-cols-2 gap-2">
        <Field label="Timeout (s)">
          <Input type="number" min={1} max={30} step={0.5} value={a.timeout_secs} onChange={(e) => set({ timeout_secs: Number(e.target.value) })} />
        </Field>
        <Field label="Save as" hint={`errors → ${a.save_as}_error`}>
          <Input value={a.save_as} onChange={(e) => set({ save_as: e.target.value })} className="font-mono text-[12px]" />
        </Field>
      </div>
      <Field label="Mock response (JSON)" hint="Used when URL is empty or API_CALL_MOCK=true">
        <JsonEditor value={a.mock_response} onChange={(m) => set({ mock_response: m })} rows={4} />
      </Field>
    </>
  );
}

export function NodeInspector({
  graph,
  node,
  issues,
  datasets,
  setGraph,
  onDelete,
  onSelect,
}: {
  graph: FlowGraph;
  node: FlowNode;
  issues: FlowIssue[];
  datasets: Dataset[];
  setGraph: (u: GraphUpdater) => void;
  onDelete: () => void;
  onSelect: (id: string) => void;
}) {
  const meta = NODE_META[node.type];
  const Icon = meta.icon;
  const change = (fn: (n: FlowNode) => FlowNode) => setGraph((g) => updateNode(g, node.id, fn));
  const vars = useMemo(() => availableVars(graph, node.id), [graph, node.id]);
  const upstreamVars = useMemo(() => availableVars(graph, node.id, false), [graph, node.id]);
  const varNames = vars.map((v) => v.name);
  const otherFields = graph.nodes.filter((n) => n.id !== node.id).flatMap((n) => n.fields.map((f) => f.name));
  const nodeIssues = issues.filter((i) => i.node_id === node.id);
  const terminal = node.type === "end" || node.type === "transfer";

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2.5" style={{ background: `color-mix(in srgb, ${meta.color} 7%, transparent)` }}>
        <span className="flex size-6 items-center justify-center rounded-md text-white" style={{ background: meta.color }}>
          <Icon className="size-3.5" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-[11px] font-semibold uppercase tracking-wider" style={{ color: meta.color }}>
            {meta.label}
          </div>
          <div className="truncate text-[12px] text-muted">{meta.description}</div>
        </div>
        {graph.initial_node !== node.id ? (
          <Button size="xs" variant="ghost" onClick={() => setGraph((g) => ({ ...g, initial_node: node.id }))} title="Make this the entry node">
            <Flag className="size-3" /> Set entry
          </Button>
        ) : (
          <Badge tone="green">entry</Badge>
        )}
        <IconButton title="Delete node" onClick={onDelete} className="hover:text-red-500">
          <Trash2 className="size-4" />
        </IconButton>
      </div>
      <div className="flex-1 overflow-y-auto thin-scroll">
        {nodeIssues.length > 0 && (
          <div className="space-y-1 border-b border-border px-3 py-2">
            {nodeIssues.map((i, k) => (
              <div key={k} className={i.severity === "error" ? "text-[11.5px] text-red-600 dark:text-red-400" : "text-[11.5px] text-amber-600 dark:text-amber-400"}>
                {i.severity === "error" ? "●" : "▲"} {i.message}
              </div>
            ))}
          </div>
        )}
        <Section title="General">
          <Field label="Label">
            <Input value={node.label} onChange={(e) => change((n) => ({ ...n, label: e.target.value }))} />
          </Field>
          <IdEditor
            node={node}
            graph={graph}
            onRename={(to) => {
              setGraph((g) => renameNode(g, node.id, to));
              onSelect(to);
            }}
          />
        </Section>
        {node.type !== "decision" && (
          <Section title="Prompt">
            {!terminal && node.type !== "lookup" && node.type !== "api_call" && (
              <Field label="Task prompt" hint="What the agent should do in this node">
                <PromptEditor value={node.task_prompt} onChange={(v) => change((n) => ({ ...n, task_prompt: v }))} vars={upstreamVars.concat(vars.filter((v) => !upstreamVars.some((u) => u.name === v.name)))} rows={6} />
              </Field>
            )}
            <Field label="Say on enter" hint="Spoken verbatim when entering (supports {{var}})">
              <Textarea rows={2} value={node.say_on_enter ?? ""} onChange={(e) => change((n) => ({ ...n, say_on_enter: e.target.value || null }))} />
            </Field>
          </Section>
        )}
        {node.type === "collect" && (
          <Section title={`Fields (${node.fields.length})`}>
            <FieldsEditor fields={node.fields} onChange={(f) => change((n) => ({ ...n, fields: f }))} otherFieldNames={otherFields} />
          </Section>
        )}
        {node.type === "lookup" && (
          <Section title="Lookup">
            <LookupForm node={node} datasets={datasets} vars={upstreamVars.map((v) => v.name)} onChange={(nn) => change(() => nn)} />
          </Section>
        )}
        {node.type === "api_call" && (
          <Section title="API call">
            <ApiForm node={node} onChange={(nn) => change(() => nn)} />
          </Section>
        )}
        {node.type === "transfer" && (
          <Section title="Transfer">
            <Field label="Transfer number" hint="E.164 or SIP URI; empty = default agent queue">
              <Input value={node.transfer_number ?? ""} onChange={(e) => change((n) => ({ ...n, transfer_number: e.target.value || null }))} placeholder="+91 22 5550 0000" className="font-mono" />
            </Field>
          </Section>
        )}
        {node.type === "end" && (
          <Section title="End">
            <Field label="End reason">
              <Input value={node.end_reason ?? ""} list="end-reasons" onChange={(e) => change((n) => ({ ...n, end_reason: e.target.value || null }))} placeholder="completed" className="font-mono" />
              <datalist id="end-reasons">
                {["completed", "booked", "not_interested", "callback_scheduled", "ineligible", "wrong_number"].map((r) => (
                  <option key={r} value={r} />
                ))}
              </datalist>
            </Field>
          </Section>
        )}
        {node.type !== "end" && node.type !== "transfer" && (
          <Section title={`Transitions (${node.transitions.length})`}>
            <TransitionsEditor node={node} nodes={graph.nodes} vars={varNames} onChange={(t) => change((n) => ({ ...n, transitions: t }))} />
          </Section>
        )}
        <Section title="Node settings" defaultOpen={false}>
          <SettingsForm node={node} nodes={graph.nodes} onChange={(s) => change((n) => ({ ...n, settings: s }))} />
        </Section>
      </div>
    </div>
  );
}

export function EdgeInspector({
  graph,
  sourceId,
  transitionId,
  setGraph,
  onDelete,
  onSelectNode,
}: {
  graph: FlowGraph;
  sourceId: string;
  transitionId: string;
  setGraph: (u: GraphUpdater) => void;
  onDelete: () => void;
  onSelectNode: (id: string) => void;
}) {
  const source = graph.nodes.find((n) => n.id === sourceId);
  const t = source?.transitions.find((x) => x.id === transitionId);
  if (!source || !t) return <EmptyInspector />;
  const vars = availableVars(graph, source.id).map((v) => v.name);
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">Transition</div>
          <div className="text-[12px]">
            <button className="font-mono text-brand hover:underline" onClick={() => onSelectNode(source.id)}>
              {source.id}
            </button>{" "}
            →{" "}
            <button className="font-mono text-brand hover:underline" onClick={() => onSelectNode(t.target)}>
              {t.target}
            </button>
          </div>
        </div>
        <IconButton title="Delete transition" onClick={onDelete} className="hover:text-red-500">
          <Trash2 className="size-4" />
        </IconButton>
      </div>
      <div className="p-3">
        <TransitionForm
          t={t}
          source={source}
          nodes={graph.nodes}
          vars={vars}
          compact
          onChange={(nt) => setGraph((g) => updateNode(g, source.id, (n) => ({ ...n, transitions: n.transitions.map((x) => (x.id === t.id ? nt : x)) })))}
          onDelete={onDelete}
        />
        <p className="mt-3 text-[11.5px] leading-relaxed text-muted">
          <b>on complete</b> fires after the node finishes (e.g. all required fields captured). <b>intent</b> becomes an LLM tool the bot calls when the
          description matches. <b>expression</b> is evaluated over captured vars by priority. <b>fallback</b> is used when nothing else matches.
        </p>
      </div>
    </div>
  );
}

export function EmptyInspector() {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-muted">
      <MousePointerClick className="size-7 text-subtle" />
      <div className="text-[13px] font-medium text-fg">Nothing selected</div>
      <div className="text-[12px]">Select a node or edge to edit it. Drag node types from the palette, and connect handles to create transitions.</div>
    </div>
  );
}
