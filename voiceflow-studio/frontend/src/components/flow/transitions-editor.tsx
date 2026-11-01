"use client";

import { CheckCircle2, Plus, Trash2, XCircle } from "lucide-react";
import { Button, Field, IconButton, Input, Select, Textarea, cn } from "@/components/ui";
import { newTransition } from "@/lib/defaults";
import { KIND_STYLE, NODE_META } from "@/lib/flow";
import { parseExpression } from "@/lib/lint";
import { TRANSITION_KINDS, type FlowNode, type Transition, type TransitionKind } from "@/lib/types";

export function ExpressionHint({ expr, vars }: { expr: string; vars: string[] }) {
  if (!expr.trim()) return <div className="text-[11px] text-muted">e.g. <code className="font-mono">loan_amount &gt; 500000 and city == &apos;Mumbai&apos;</code></div>;
  const p = parseExpression(expr);
  if (p.error)
    return (
      <div className="flex items-center gap-1 text-[11px] text-red-500">
        <XCircle className="size-3" /> {p.error}
      </div>
    );
  const unknown = p.names.filter((n) => !vars.includes(n));
  if (unknown.length)
    return (
      <div className="flex items-center gap-1 text-[11px] text-amber-600 dark:text-amber-400">
        <XCircle className="size-3" /> Unknown vars: {unknown.join(", ")}
      </div>
    );
  return (
    <div className="flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
      <CheckCircle2 className="size-3" /> Valid{p.names.length ? ` · uses ${p.names.join(", ")}` : ""}
    </div>
  );
}

export function allowedKinds(source: FlowNode): TransitionKind[] {
  return source.type === "decision" ? ["expression", "intent", "fallback"] : TRANSITION_KINDS;
}

export function TransitionForm({
  t,
  source,
  nodes,
  vars,
  onChange,
  onDelete,
  compact,
}: {
  t: Transition;
  source: FlowNode;
  nodes: FlowNode[];
  vars: string[];
  onChange: (t: Transition) => void;
  onDelete: () => void;
  compact?: boolean;
}) {
  const st = KIND_STYLE[t.kind];
  const missing = !nodes.some((n) => n.id === t.target);
  return (
    <div className={cn("space-y-2 rounded-md border bg-panel p-2", missing ? "border-red-500/50" : "border-border")} style={{ borderLeft: `3px solid ${st.stroke}` }}>
      <div className="flex items-center gap-1.5">
        <Select value={t.kind} onChange={(e) => onChange({ ...t, kind: e.target.value as TransitionKind })} className="h-7 w-[130px] shrink-0 text-[12px]">
          {allowedKinds(source).map((k) => (
            <option key={k} value={k}>
              {KIND_STYLE[k].label}
            </option>
          ))}
        </Select>
        <span className="text-subtle">→</span>
        <Select value={t.target} onChange={(e) => onChange({ ...t, target: e.target.value })} className={cn("h-7 min-w-0 flex-1 text-[12px]", missing && "border-red-500")}>
          {missing && <option value={t.target}>⚠ {t.target || "select target"}</option>}
          {nodes.map((n) => (
            <option key={n.id} value={n.id}>
              {NODE_META[n.type].label}: {n.label} ({n.id})
            </option>
          ))}
        </Select>
        {!compact && (
          <IconButton title="Delete transition" onClick={onDelete} className="size-6 hover:text-red-500">
            <Trash2 className="size-3" />
          </IconButton>
        )}
      </div>
      {t.kind === "intent" && (
        <Field label="When (LLM tool description)">
          <Textarea rows={2} value={t.condition} onChange={(e) => onChange({ ...t, condition: e.target.value })} placeholder="Caller wants to book a test drive" />
        </Field>
      )}
      {t.kind === "expression" && (
        <div className="space-y-1">
          <Input value={t.condition} onChange={(e) => onChange({ ...t, condition: e.target.value })} placeholder="dealers_found == True" className="font-mono text-[12px]" />
          <ExpressionHint expr={t.condition} vars={vars} />
        </div>
      )}
      <div className="grid grid-cols-[1fr_80px] gap-2">
        <Field label="Label">
          <Input value={t.label} onChange={(e) => onChange({ ...t, label: e.target.value })} placeholder="optional" className="h-7 text-[12px]" />
        </Field>
        <Field label="Priority">
          <Input type="number" value={t.priority} onChange={(e) => onChange({ ...t, priority: Number(e.target.value) })} className="h-7 text-[12px]" disabled={t.kind !== "expression"} title="Lower is evaluated first among expressions" />
        </Field>
      </div>
    </div>
  );
}

export function TransitionsEditor({
  node,
  nodes,
  vars,
  onChange,
}: {
  node: FlowNode;
  nodes: FlowNode[];
  vars: string[];
  onChange: (t: Transition[]) => void;
}) {
  const list = node.transitions;
  return (
    <div className="space-y-1.5">
      {list.map((t, i) => (
        <TransitionForm
          key={t.id}
          t={t}
          source={node}
          nodes={nodes}
          vars={vars}
          onChange={(nt) => onChange(list.map((x, j) => (j === i ? nt : x)))}
          onDelete={() => onChange(list.filter((_, j) => j !== i))}
        />
      ))}
      {list.length === 0 && <div className="rounded-md border border-dashed border-red-500/40 p-2.5 text-center text-[12px] text-red-500">No transitions — this node is a dead end.</div>}
      <Button
        size="sm"
        variant="outline"
        onClick={() => {
          const target = nodes.find((n) => n.id !== node.id && n.type === "end")?.id ?? nodes.find((n) => n.id !== node.id)?.id ?? "";
          const kind: TransitionKind = node.type === "decision" ? "expression" : node.type === "conversation" || node.type === "start" ? "intent" : "on_complete";
          onChange([...list, newTransition(target, kind)]);
        }}
      >
        <Plus className="size-3.5" /> Add transition
      </Button>
    </div>
  );
}
