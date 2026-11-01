"use client";

import {
  BaseEdge,
  EdgeLabelRenderer,
  Handle,
  Position,
  getBezierPath,
  type EdgeProps,
  type NodeProps,
} from "@xyflow/react";
import { AlertCircle, AlertTriangle, Lock, Zap } from "lucide-react";
import { memo } from "react";
import { cn } from "@/components/ui";
import { KIND_STYLE, NODE_META, NODE_W, branchHandles, hasSource, transitionText, type RFEdge, type RFNode } from "@/lib/flow";

function FlowNodeView({ data, selected }: NodeProps<RFNode>) {
  const { node, issues, isInitial, highlight, readOnly } = data;
  const meta = NODE_META[node.type];
  const Icon = meta.icon;
  const errors = issues.filter((i) => i.severity === "error");
  const warnings = issues.filter((i) => i.severity === "warning");
  const branches = branchHandles(node.type);
  const subtitle = node.say_on_enter || node.task_prompt;

  return (
    <div
      style={{ width: NODE_W, ["--accent" as string]: meta.color }}
      className={cn(
        "group relative rounded-[10px] border bg-panel text-left shadow-sm transition-shadow",
        selected ? "border-[var(--accent)] shadow-lg ring-2 ring-[color-mix(in_srgb,var(--accent)_30%,transparent)]" : "border-border hover:shadow-md",
        highlight === "current" && "pulse-ring border-[var(--accent)] ring-2 ring-[var(--accent)]",
        highlight === "visited" && "border-[color-mix(in_srgb,var(--accent)_60%,transparent)]",
        readOnly && !highlight && "opacity-90",
      )}
    >
      <Handle type="target" position={Position.Top} id="in" isConnectable={!readOnly} />

      {/* header */}
      <div
        className={cn(
          "flex items-center gap-2 rounded-t-[9px] border-b border-border px-2.5 py-1.5",
          node.type === "decision" && "bg-[repeating-linear-gradient(135deg,color-mix(in_srgb,var(--accent)_10%,transparent)_0_6px,transparent_6px_12px)]",
        )}
        style={{ background: node.type === "decision" ? undefined : `color-mix(in srgb, ${meta.color} 9%, transparent)` }}
      >
        <span
          className={cn("flex size-5 shrink-0 items-center justify-center text-white", node.type === "decision" ? "rotate-45 rounded-[4px]" : "rounded-md")}
          style={{ background: meta.color }}
        >
          <Icon className={cn("size-3", node.type === "decision" && "-rotate-45")} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[12.5px] font-semibold leading-4">{node.label || node.id}</div>
          <div className="truncate font-mono text-[10px] leading-3 text-subtle">{node.id}</div>
        </div>
        {isInitial && (
          <span className="rounded bg-emerald-500/15 px-1 text-[9px] font-bold uppercase tracking-wider text-emerald-600 dark:text-emerald-400">entry</span>
        )}
        {errors.length > 0 && (
          <span title={errors.map((e) => e.message).join("\n")} className="flex items-center gap-0.5 rounded bg-red-500/15 px-1 text-[10px] font-semibold text-red-600 dark:text-red-400">
            <AlertCircle className="size-3" />
            {errors.length}
          </span>
        )}
        {warnings.length > 0 && (
          <span title={warnings.map((e) => e.message).join("\n")} className="flex items-center gap-0.5 rounded bg-amber-500/15 px-1 text-[10px] font-semibold text-amber-600 dark:text-amber-400">
            <AlertTriangle className="size-3" />
            {warnings.length}
          </span>
        )}
      </div>

      {/* body */}
      <div className="space-y-1.5 px-2.5 py-2">
        {subtitle ? (
          <p className="line-clamp-2 text-[11px] leading-snug text-muted">{subtitle}</p>
        ) : (
          <p className="text-[11px] italic text-subtle">{meta.description}</p>
        )}

        {node.type === "collect" && (
          <div className="flex flex-wrap gap-1">
            {node.fields.length === 0 && <span className="text-[11px] text-red-500">No fields</span>}
            {node.fields.map((f) => (
              <span
                key={f.name}
                title={`${f.label} · ${f.type}${f.required ? " · required" : ""}${f.sensitive ? " · sensitive" : ""}`}
                className="inline-flex items-center gap-0.5 rounded border border-blue-500/20 bg-blue-500/8 px-1 py-px font-mono text-[10px] text-blue-700 dark:text-blue-300"
              >
                {f.sensitive && <Lock className="size-2.5" />}
                {f.name}
                {f.required && <span className="text-red-500">*</span>}
              </span>
            ))}
          </div>
        )}
        {node.type === "lookup" && (
          <div className="flex items-center gap-1 text-[11px]">
            <span className="rounded bg-cyan-500/10 px-1 font-mono text-cyan-700 dark:text-cyan-300">{node.lookup?.dataset || "no dataset"}</span>
            <span className="text-subtle">→</span>
            <span className="font-mono text-muted">{node.lookup?.save_as}</span>
          </div>
        )}
        {node.type === "api_call" && (
          <div className="flex items-center gap-1 truncate font-mono text-[10.5px] text-muted">
            <span className="rounded bg-slate-500/15 px-1 font-semibold text-slate-600 dark:text-slate-300">{node.api_call?.method}</span>
            <span className="truncate">{node.api_call?.url || (node.api_call?.mock_response ? "mock response" : "no url")}</span>
          </div>
        )}
        {node.type === "transfer" && <div className="font-mono text-[11px] text-orange-600 dark:text-orange-400">☎ {node.transfer_number || "default agent queue"}</div>}
        {node.type === "end" && <div className="text-[11px] text-muted">reason: <span className="font-mono text-fg">{node.end_reason || "completed"}</span></div>}
        {(node.type === "conversation" || node.type === "start") && node.transitions.some((t) => t.kind === "intent") && (
          <div className="flex items-center gap-1 text-[10.5px] text-violet-600 dark:text-violet-400">
            <Zap className="size-3" />
            {node.transitions.filter((t) => t.kind === "intent").length} intents
          </div>
        )}
      </div>

      {/* branch handles */}
      {branches && hasSource(node.type) && (
        <div className="relative flex h-7 items-stretch border-t border-border">
          {node.transitions.length === 0 && <div className="flex-1 px-2 py-1 text-[10px] text-subtle">drag to add branch</div>}
          {node.transitions.map((t) => {
            const st = KIND_STYLE[t.kind];
            return (
              <div key={t.id} className="relative flex min-w-0 flex-1 items-center justify-center px-1" title={transitionText(t)}>
                <span className="truncate font-mono text-[9.5px]" style={{ color: st.stroke }}>
                  {t.kind === "fallback" ? "else" : transitionText(t)}
                </span>
                <Handle
                  type="source"
                  position={Position.Bottom}
                  id={t.id}
                  isConnectable={false}
                  style={{ left: "50%", background: st.stroke }}
                />
              </div>
            );
          })}
          {!readOnly && (
            <Handle
              type="source"
              position={Position.Right}
              id="out"
              title="Drag to add a branch"
              style={{ top: "auto", bottom: 8, background: "var(--accent)" }}
            />
          )}
        </div>
      )}
      {!branches && hasSource(node.type) && <Handle type="source" position={Position.Bottom} id="out" isConnectable={!readOnly} style={{ background: meta.color }} />}
    </div>
  );
}

export const FlowNodeComponent = memo(FlowNodeView);

function TransitionEdgeView({ id, sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition, data, selected, markerEnd }: EdgeProps<RFEdge>) {
  const t = data!.transition;
  const st = KIND_STYLE[t.kind];
  const [path, lx, ly] = getBezierPath({ sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition });
  const text = transitionText(t);
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        style={{ stroke: st.stroke, strokeWidth: selected ? 2.5 : 1.6, strokeDasharray: st.dash }}
        interactionWidth={18}
      />
      {text && (
        <EdgeLabelRenderer>
          <div
            className={cn(
              "nodrag nopan pointer-events-auto absolute max-w-[180px] cursor-pointer truncate rounded-md border bg-panel px-1.5 py-px text-[10px] shadow-sm",
              t.kind === "expression" ? "font-mono" : "",
              selected ? "border-brand" : "border-border",
            )}
            style={{ transform: `translate(-50%, -50%) translate(${lx}px, ${ly}px)`, color: st.stroke }}
            title={`${st.label}: ${t.condition || text}`}
          >
            {text}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export const TransitionEdge = memo(TransitionEdgeView);

export const nodeTypes = { flow: FlowNodeComponent };
export const edgeTypes = { transition: TransitionEdge };
