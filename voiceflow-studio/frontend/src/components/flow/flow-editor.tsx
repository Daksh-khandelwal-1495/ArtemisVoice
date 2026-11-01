"use client";

import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Connection,
  type EdgeChange,
  type NodeChange,
} from "@xyflow/react";
import { AlertCircle, AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, LayoutDashboard, Redo2, Settings2, Undo2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useState, type DragEvent } from "react";
import { Badge, Button, Drawer, Field, IconButton, Select, Textarea, cn, useConfirm } from "@/components/ui";
import { KVEditor } from "@/components/ui/editors";
import { newNode, newTransition } from "@/lib/defaults";
import { NODE_META, NODE_TYPES_ORDER, autoLayout, removeNode, toRFEdges, toRFNodes, updateNode, type RFEdge, type RFNode } from "@/lib/flow";
import type { Dataset, FlowGraph, FlowIssue, NodeType, TransitionKind } from "@/lib/types";
import type { GraphHistory } from "@/lib/use-graph-history";
import { useIsDark } from "@/lib/use-theme";
import { EdgeInspector, EmptyInspector, NodeInspector } from "./inspector";
import { edgeTypes, nodeTypes } from "./nodes";

/** React Flow reports node sizes via `dimensions` changes; controlled nodes must carry them back as `measured`. */
function useMeasured() {
  const [sizes, setSizes] = useState<Record<string, { width: number; height: number }>>({});
  const onDims = useCallback((changes: NodeChange<RFNode>[]) => {
    const dims = changes.filter((c) => c.type === "dimensions" && c.dimensions);
    if (!dims.length) return;
    setSizes((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const c of dims) {
        if (c.type !== "dimensions" || !c.dimensions) continue;
        const p = prev[c.id];
        if (!p || p.width !== c.dimensions.width || p.height !== c.dimensions.height) {
          next[c.id] = c.dimensions;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, []);
  const withMeasured = useCallback((nodes: RFNode[]) => nodes.map((n) => (sizes[n.id] ? { ...n, measured: sizes[n.id] } : n)), [sizes]);
  return { onDims, withMeasured };
}

export type Selection = { kind: "node"; id: string } | { kind: "edge"; source: string; transition: string } | null;

const DND_TYPE = "application/vf-node";

function Palette({ onAdd, hasStart }: { onAdd: (t: NodeType) => void; hasStart: boolean }) {
  return (
    <div className="flex w-[188px] shrink-0 flex-col border-r border-border bg-panel">
      <div className="px-3 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wider text-muted">Nodes</div>
      <div className="flex-1 space-y-1 overflow-y-auto p-2 thin-scroll">
        {NODE_TYPES_ORDER.map((t) => {
          const m = NODE_META[t];
          const Icon = m.icon;
          const disabled = t === "start" && hasStart;
          return (
            <div
              key={t}
              draggable={!disabled}
              onDragStart={(e) => {
                e.dataTransfer.setData(DND_TYPE, t);
                e.dataTransfer.effectAllowed = "move";
              }}
              onDoubleClick={() => !disabled && onAdd(t)}
              title={disabled ? "Only one start node allowed" : "Drag onto the canvas (or double-click)"}
              className={cn(
                "flex cursor-grab items-center gap-2 rounded-md border border-border bg-panel px-2 py-1.5 transition-colors hover:border-[var(--accent)] hover:bg-panel-2 active:cursor-grabbing",
                disabled && "cursor-not-allowed opacity-40",
              )}
              style={{ ["--accent" as string]: m.color }}
            >
              <span className={cn("flex size-5 items-center justify-center text-white", t === "decision" ? "rotate-45 rounded-[4px]" : "rounded-md")} style={{ background: m.color }}>
                <Icon className={cn("size-3", t === "decision" && "-rotate-45")} />
              </span>
              <div className="min-w-0">
                <div className="text-[12.5px] font-medium leading-4">{m.label}</div>
                <div className="truncate text-[10.5px] leading-3 text-muted">{m.description}</div>
              </div>
            </div>
          );
        })}
      </div>
      <div className="border-t border-border p-3 text-[10.5px] leading-relaxed text-subtle">
        <kbd className="rounded border border-border px-1">⌘Z</kbd> undo · <kbd className="rounded border border-border px-1">⌘⇧Z</kbd> redo · <kbd className="rounded border border-border px-1">Del</kbd> delete ·{" "}
        <kbd className="rounded border border-border px-1">⌘S</kbd> save
      </div>
    </div>
  );
}

export function ValidationPanel({
  issues,
  source,
  onFocus,
  graph,
}: {
  issues: FlowIssue[];
  source: "server" | "lint";
  onFocus: (nodeId: string) => void;
  graph: FlowGraph;
}) {
  const [open, setOpen] = useState(false);
  const errors = issues.filter((i) => i.severity === "error").length;
  const warnings = issues.length - errors;
  const label = (id?: string | null) => graph.nodes.find((n) => n.id === id)?.label ?? id;
  return (
    <div className="absolute bottom-3 left-3 z-10 w-[min(560px,calc(100%-24px))] overflow-hidden rounded-lg border border-border bg-panel shadow-lg">
      <button className="flex w-full items-center gap-2 px-3 py-2 text-[12px]" onClick={() => setOpen(!open)}>
        {issues.length === 0 ? (
          <span className="flex items-center gap-1 font-medium text-emerald-600 dark:text-emerald-400">
            <CheckCircle2 className="size-3.5" /> No issues
          </span>
        ) : (
          <>
            <span className="flex items-center gap-1 font-medium text-red-600 dark:text-red-400">
              <AlertCircle className="size-3.5" /> {errors} errors
            </span>
            <span className="flex items-center gap-1 font-medium text-amber-600 dark:text-amber-400">
              <AlertTriangle className="size-3.5" /> {warnings} warnings
            </span>
          </>
        )}
        <Badge className="ml-1">{source === "server" ? "server validated" : "live lint"}</Badge>
        <span className="ml-auto text-muted">{open ? <ChevronDown className="size-4" /> : <ChevronUp className="size-4" />}</span>
      </button>
      {open && issues.length > 0 && (
        <div className="max-h-56 overflow-y-auto border-t border-border thin-scroll">
          {issues.map((i, k) => (
            <button
              key={k}
              disabled={!i.node_id}
              onClick={() => i.node_id && onFocus(i.node_id)}
              className="flex w-full items-start gap-2 border-b border-border px-3 py-1.5 text-left text-[12px] last:border-0 hover:bg-panel-2"
            >
              {i.severity === "error" ? <AlertCircle className="mt-0.5 size-3.5 shrink-0 text-red-500" /> : <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-amber-500" />}
              <span className="flex-1">{i.message}</span>
              {i.node_id && <span className="shrink-0 font-mono text-[11px] text-muted">{label(i.node_id)}</span>}
              <span className="shrink-0 font-mono text-[10px] text-subtle">{i.code}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function FlowSettingsDrawer({ open, onClose, graph, setGraph }: { open: boolean; onClose: () => void; graph: FlowGraph; setGraph: GraphHistory["set"] }) {
  const vars = Object.fromEntries(Object.entries(graph.variables ?? {}).map(([k, v]) => [k, typeof v === "string" ? v : JSON.stringify(v)]));
  return (
    <Drawer open={open} onClose={onClose} title="Flow settings">
      <div className="space-y-4">
        <Field label="Global prompt" hint="Persona / role message prepended to every node">
          <Textarea rows={12} value={graph.global_prompt} onChange={(e) => setGraph((g) => ({ ...g, global_prompt: e.target.value }))} className="text-[12.5px]" />
        </Field>
        <Field label="Initial node">
          <Select value={graph.initial_node} onChange={(e) => setGraph((g) => ({ ...g, initial_node: e.target.value }))}>
            {graph.nodes.map((n) => (
              <option key={n.id} value={n.id}>
                {n.label} ({n.id})
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Static variables" hint="Available as {{var}} in every prompt. JSON values are parsed.">
          <KVEditor
            value={vars}
            onChange={(kv) =>
              setGraph((g) => ({
                ...g,
                variables: Object.fromEntries(
                  Object.entries(kv).map(([k, v]) => {
                    if (/^(\{|\[|true$|false$|null$|-?\d+(\.\d+)?$)/.test(v.trim())) {
                      try {
                        return [k, JSON.parse(v)];
                      } catch {}
                    }
                    return [k, v];
                  }),
                ),
              }))
            }
          />
        </Field>
      </div>
    </Drawer>
  );
}

interface EditorProps {
  history: GraphHistory;
  issues: FlowIssue[];
  issueSource: "server" | "lint";
  datasets: Dataset[];
  selection: Selection;
  setSelection: (s: Selection) => void;
}

function EditorInner({ history, issues, issueSource, datasets, selection, setSelection }: EditorProps) {
  const graph = history.graph!;
  const { set, checkpoint, undo, redo, canUndo, canRedo } = history;
  const rf = useReactFlow();
  const dark = useIsDark();
  const confirm = useConfirm();
  const [settingsOpen, setSettingsOpen] = useState(false);

  const { onDims, withMeasured } = useMeasured();
  const nodes = useMemo(
    () => withMeasured(toRFNodes(graph, issues, { selectedId: selection?.kind === "node" ? selection.id : null })),
    [graph, issues, selection, withMeasured],
  );
  const edges = useMemo(
    () =>
      toRFEdges(graph, { selectedId: selection?.kind === "edge" ? `${selection.source}::${selection.transition}` : null }).map((e) => ({
        ...e,
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: "#8b8b96" },
      })),
    [graph, selection],
  );

  const onNodesChange = useCallback(
    (changes: NodeChange<RFNode>[]) => {
      onDims(changes);
      const pos = changes.filter((c) => c.type === "position" && c.position);
      if (pos.length) {
        set((g) => {
          const m = new Map(pos.map((c) => [c.type === "position" ? c.id : "", c.type === "position" ? c.position! : { x: 0, y: 0 }]));
          return { ...g, nodes: g.nodes.map((n) => (m.has(n.id) ? { ...n, position: m.get(n.id)! } : n)) };
        }, false);
      }
      for (const c of changes) {
        if (c.type === "select" && c.selected) setSelection({ kind: "node", id: c.id });
      }
    },
    [set, setSelection, onDims],
  );

  const onEdgesChange = useCallback(
    (changes: EdgeChange<RFEdge>[]) => {
      for (const c of changes) {
        if (c.type === "select" && c.selected) {
          const [source, transition] = c.id.split("::");
          setSelection({ kind: "edge", source, transition });
        }
      }
    },
    [setSelection],
  );

  const deleteNodes = useCallback(
    async (ids: string[]) => {
      if (!ids.length) return;
      const names = ids.map((id) => graph.nodes.find((n) => n.id === id)?.label ?? id).join(", ");
      const ok = await confirm({ title: `Delete ${ids.length > 1 ? `${ids.length} nodes` : `“${names}”`}?`, message: "Transitions pointing to it are removed too. You can undo with ⌘Z.", confirmLabel: "Delete", danger: true });
      if (!ok) return;
      set((g) => ids.reduce((acc, id) => removeNode(acc, id), g));
      setSelection(null);
    },
    [graph.nodes, confirm, set, setSelection],
  );

  const deleteTransition = useCallback(
    (source: string, tid: string) => {
      set((g) => updateNode(g, source, (n) => ({ ...n, transitions: n.transitions.filter((t) => t.id !== tid) })));
      setSelection(null);
    },
    [set, setSelection],
  );

  const onConnect = useCallback(
    (c: Connection) => {
      if (!c.source || !c.target) return;
      const src = graph.nodes.find((n) => n.id === c.source);
      if (!src) return;
      let kind: TransitionKind = "on_complete";
      let condition = "";
      if (src.type === "decision") kind = src.transitions.some((t) => t.kind === "fallback") ? "expression" : src.transitions.length ? "fallback" : "expression";
      else if (src.type === "lookup") {
        const save = src.lookup?.save_as || "result";
        if (!src.transitions.some((t) => t.kind === "expression")) {
          kind = "expression";
          condition = `${save}_found == True`;
        } else kind = "fallback";
      } else if (src.type === "start" || src.type === "conversation") kind = "intent";
      else if (src.transitions.some((t) => t.kind === "on_complete")) kind = "fallback";
      const t = { ...newTransition(c.target, kind), condition };
      set((g) => updateNode(g, src.id, (n) => ({ ...n, transitions: [...n.transitions, t] })));
      setSelection({ kind: "edge", source: src.id, transition: t.id });
    },
    [graph.nodes, set, setSelection],
  );

  const addNode = useCallback(
    (type: NodeType, position?: { x: number; y: number }) => {
      const pos = position ?? rf.screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 });
      const n = newNode(type, graph.nodes.map((x) => x.id), { x: Math.round(pos.x / 16) * 16, y: Math.round(pos.y / 16) * 16 });
      set((g) => ({ ...g, nodes: [...g.nodes, n], initial_node: g.nodes.length === 0 ? n.id : g.initial_node }));
      setSelection({ kind: "node", id: n.id });
    },
    [graph.nodes, rf, set, setSelection],
  );

  const onDrop = useCallback(
    (e: DragEvent) => {
      e.preventDefault();
      const t = e.dataTransfer.getData(DND_TYPE) as NodeType;
      if (!t || !NODE_META[t]) return;
      addNode(t, rf.screenToFlowPosition({ x: e.clientX - 120, y: e.clientY - 30 }));
    },
    [addNode, rf],
  );

  const focusNode = useCallback(
    (id: string) => {
      const n = graph.nodes.find((x) => x.id === id);
      if (!n) return;
      setSelection({ kind: "node", id });
      rf.setCenter(n.position.x + 124, n.position.y + 60, { zoom: Math.max(rf.getZoom(), 1), duration: 400 });
    },
    [graph.nodes, rf, setSelection],
  );

  const layout = () => {
    set((g) => autoLayout(g));
    setTimeout(() => rf.fitView({ padding: 0.1, minZoom: 0.45, maxZoom: 1, duration: 400 }), 30);
  };

  // keyboard: undo/redo/delete
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const tgt = e.target as HTMLElement;
      if (tgt.closest("input, textarea, select, [contenteditable=true]")) return;
      const mod = e.metaKey || e.ctrlKey;
      if (mod && e.key.toLowerCase() === "z") {
        e.preventDefault();
        if (e.shiftKey) redo();
        else undo();
      } else if (mod && e.key.toLowerCase() === "y") {
        e.preventDefault();
        redo();
      } else if ((e.key === "Delete" || e.key === "Backspace") && selection) {
        e.preventDefault();
        if (selection.kind === "node") deleteNodes([selection.id]);
        else deleteTransition(selection.source, selection.transition);
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [undo, redo, selection, deleteNodes, deleteTransition]);

  const selectedNode = selection?.kind === "node" ? graph.nodes.find((n) => n.id === selection.id) : undefined;

  return (
    <div className="flex min-h-0 flex-1">
      <Palette onAdd={(t) => addNode(t)} hasStart={graph.nodes.some((n) => n.type === "start")} />
      <div className="relative min-w-0 flex-1" onDragOver={(e) => (e.preventDefault(), (e.dataTransfer.dropEffect = "move"))} onDrop={onDrop}>
        <ReactFlow<RFNode, RFEdge>
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeDragStart={() => checkpoint()}
          onPaneClick={() => setSelection(null)}
          deleteKeyCode={null}
          snapToGrid
          snapGrid={[16, 16]}
          fitView
          fitViewOptions={{ padding: 0.1, minZoom: 0.5, maxZoom: 1 }}
          minZoom={0.2}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
          colorMode={dark ? "dark" : "light"}
        >
          <Background variant={BackgroundVariant.Dots} gap={16} size={1.2} color="var(--canvas-dot)" />
          <Controls position="top-right" showInteractive={false} />
          <MiniMap position="bottom-right" pannable zoomable nodeColor={(n) => NODE_META[(n as RFNode).data.node.type].color} nodeStrokeWidth={0} />
        </ReactFlow>
        <div className="absolute left-3 top-3 z-10 flex items-center gap-0.5 rounded-lg border border-border bg-panel p-0.5 shadow-sm">
          <IconButton title="Undo (⌘Z)" onClick={undo} disabled={!canUndo}>
            <Undo2 className="size-4" />
          </IconButton>
          <IconButton title="Redo (⌘⇧Z)" onClick={redo} disabled={!canRedo}>
            <Redo2 className="size-4" />
          </IconButton>
          <div className="mx-0.5 h-5 w-px bg-border" />
          <Button size="sm" variant="ghost" onClick={layout} title="Auto-layout (dagre)">
            <LayoutDashboard className="size-3.5" /> Auto-layout
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSettingsOpen(true)}>
            <Settings2 className="size-3.5" /> Flow settings
          </Button>
          <div className="mx-0.5 h-5 w-px bg-border" />
          <span className="px-1.5 text-[11px] text-muted">{graph.nodes.length} nodes</span>
        </div>
        <ValidationPanel issues={issues} source={issueSource} onFocus={focusNode} graph={graph} />
      </div>
      <aside className="w-[360px] shrink-0 border-l border-border bg-panel">
        {selectedNode ? (
          <NodeInspector
            key={selectedNode.id}
            graph={graph}
            node={selectedNode}
            issues={issues}
            datasets={datasets}
            setGraph={(u) => set(u)}
            onDelete={() => deleteNodes([selectedNode.id])}
            onSelect={(id) => setSelection({ kind: "node", id })}
          />
        ) : selection?.kind === "edge" ? (
          <EdgeInspector
            graph={graph}
            sourceId={selection.source}
            transitionId={selection.transition}
            setGraph={(u) => set(u)}
            onDelete={() => deleteTransition(selection.source, selection.transition)}
            onSelectNode={focusNode}
          />
        ) : (
          <EmptyInspector />
        )}
      </aside>
      <FlowSettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} graph={graph} setGraph={set} />
    </div>
  );
}

export function FlowEditor(props: EditorProps) {
  return (
    <ReactFlowProvider>
      <EditorInner {...props} />
    </ReactFlowProvider>
  );
}

/** Read-only canvas (test call, versions). Highlights current node + visited path. */
function ReadOnlyInner({ graph, current, visited, fitKey }: { graph: FlowGraph; current?: string | null; visited?: string[]; fitKey?: string }) {
  const rf = useReactFlow();
  const dark = useIsDark();
  const visitedSet = useMemo(() => new Set(visited ?? []), [visited]);
  const visitedEdges = useMemo(() => {
    const s = new Set<string>();
    (visited ?? []).forEach((v, i, a) => i > 0 && s.add(`${a[i - 1]}->${v}`));
    return s;
  }, [visited]);
  const { onDims, withMeasured } = useMeasured();
  const nodes = useMemo(() => withMeasured(toRFNodes(graph, [], { current, visited: visitedSet, readOnly: true })), [graph, current, visitedSet, withMeasured]);
  const edges = useMemo(
    () =>
      toRFEdges(graph, { visitedEdges }).map((e) => ({
        ...e,
        markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: "#8b8b96" },
        style: visitedEdges.has(`${e.source}->${e.target}`) ? { strokeWidth: 2.5 } : { opacity: 0.55 },
      })),
    [graph, visitedEdges],
  );
  useEffect(() => {
    if (!current) return;
    const n = graph.nodes.find((x) => x.id === current);
    if (n) rf.setCenter(n.position.x + 124, n.position.y + 60, { zoom: 0.9, duration: 500 });
  }, [current, graph.nodes, rf]);
  useEffect(() => {
    const t = setTimeout(() => rf.fitView({ padding: 0.15 }), 50);
    return () => clearTimeout(t);
  }, [fitKey, rf]);
  return (
    <ReactFlow<RFNode, RFEdge>
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodesChange={onDims}
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={false}
      fitView
      minZoom={0.15}
      proOptions={{ hideAttribution: true }}
      colorMode={dark ? "dark" : "light"}
    >
      <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="var(--canvas-dot)" />
      <Controls position="top-right" showInteractive={false} />
    </ReactFlow>
  );
}

export function ReadOnlyFlow(props: { graph: FlowGraph; current?: string | null; visited?: string[]; fitKey?: string }) {
  return (
    <ReactFlowProvider>
      <ReadOnlyInner {...props} />
    </ReactFlowProvider>
  );
}
