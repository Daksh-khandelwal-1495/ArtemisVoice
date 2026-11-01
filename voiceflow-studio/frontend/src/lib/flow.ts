import dagre from "@dagrejs/dagre";
import {
  Database,
  Flag,
  GitBranch,
  ListChecks,
  MessagesSquare,
  PhoneForwarded,
  Play,
  Webhook,
  type LucideIcon,
} from "lucide-react";
import type { Edge, Node } from "@xyflow/react";
import type { FlowGraph, FlowIssue, FlowNode, NodeType, Transition } from "./types";

export interface NodeMeta {
  label: string;
  description: string;
  icon: LucideIcon;
  color: string; // accent hex
}

export const NODE_META: Record<NodeType, NodeMeta> = {
  start: { label: "Start", description: "Greeting & persona entry point", icon: Play, color: "#16a34a" },
  collect: { label: "Collect", description: "Capture validated fields", icon: ListChecks, color: "#2563eb" },
  conversation: { label: "Conversation", description: "Free Q&A with intent exits", icon: MessagesSquare, color: "#7c3aed" },
  decision: { label: "Decision", description: "Branch on expressions", icon: GitBranch, color: "#d97706" },
  lookup: { label: "Lookup", description: "Query a dataset", icon: Database, color: "#0891b2" },
  api_call: { label: "API call", description: "Call an HTTP endpoint", icon: Webhook, color: "#475569" },
  transfer: { label: "Transfer", description: "Hand off to a human", icon: PhoneForwarded, color: "#ea580c" },
  end: { label: "End", description: "Finish the call", icon: Flag, color: "#dc2626" },
};

export const NODE_TYPES_ORDER: NodeType[] = ["start", "collect", "conversation", "decision", "lookup", "api_call", "transfer", "end"];

/** Node types whose outgoing edges use one handle per transition. */
export const branchHandles = (t: NodeType) => t === "decision" || t === "lookup";
export const hasSource = (t: NodeType) => t !== "end" && t !== "transfer";

export type Highlight = "current" | "visited" | undefined;

export interface FlowNodeData extends Record<string, unknown> {
  node: FlowNode;
  issues: FlowIssue[];
  isInitial: boolean;
  highlight?: Highlight;
  readOnly?: boolean;
}

export interface TransitionEdgeData extends Record<string, unknown> {
  transition: Transition;
  sourceId: string;
}

export type RFNode = Node<FlowNodeData, "flow">;
export type RFEdge = Edge<TransitionEdgeData, "transition">;

export function toRFNodes(
  graph: FlowGraph,
  issues: FlowIssue[],
  opts: { selectedId?: string | null; current?: string | null; visited?: Set<string>; readOnly?: boolean } = {},
): RFNode[] {
  const byNode = new Map<string, FlowIssue[]>();
  issues.forEach((i) => i.node_id && byNode.set(i.node_id, [...(byNode.get(i.node_id) ?? []), i]));
  return graph.nodes.map((n) => ({
    id: n.id,
    type: "flow",
    position: { x: n.position?.x ?? 0, y: n.position?.y ?? 0 },
    selected: opts.selectedId === n.id,
    deletable: !opts.readOnly,
    data: {
      node: n,
      issues: byNode.get(n.id) ?? [],
      isInitial: graph.initial_node === n.id,
      highlight: opts.current === n.id ? "current" : opts.visited?.has(n.id) ? "visited" : undefined,
      readOnly: opts.readOnly,
    },
  }));
}

export function edgeId(sourceId: string, t: Transition) {
  return `${sourceId}::${t.id}`;
}

export function toRFEdges(graph: FlowGraph, opts: { selectedId?: string | null; visitedEdges?: Set<string> } = {}): RFEdge[] {
  const ids = new Set(graph.nodes.map((n) => n.id));
  const edges: RFEdge[] = [];
  for (const n of graph.nodes) {
    if (!hasSource(n.type)) continue;
    for (const t of n.transitions) {
      if (!ids.has(t.target)) continue;
      const id = edgeId(n.id, t);
      edges.push({
        id,
        source: n.id,
        target: t.target,
        sourceHandle: branchHandles(n.type) ? t.id : "out",
        targetHandle: "in",
        type: "transition",
        selected: opts.selectedId === id,
        animated: opts.visitedEdges?.has(`${n.id}->${t.target}`),
        data: { transition: t, sourceId: n.id },
      });
    }
  }
  return edges;
}

export const NODE_W = 248;

export function estimateHeight(n: FlowNode) {
  let h = 64;
  if (n.task_prompt || n.say_on_enter) h += 34;
  if (n.type === "collect") h += 26 + Math.ceil(n.fields.length / 3) * 22;
  if (n.type === "lookup" || n.type === "api_call" || n.type === "transfer" || n.type === "end") h += 24;
  if (branchHandles(n.type)) h += 28;
  return h;
}

export function autoLayout(graph: FlowGraph, direction: "TB" | "LR" = "TB"): FlowGraph {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: direction, nodesep: 60, ranksep: 90, marginx: 20, marginy: 20 });
  g.setDefaultEdgeLabel(() => ({}));
  graph.nodes.forEach((n) => g.setNode(n.id, { width: NODE_W, height: estimateHeight(n) }));
  const ids = new Set(graph.nodes.map((n) => n.id));
  graph.nodes.forEach((n) => n.transitions.forEach((t) => ids.has(t.target) && t.target !== n.id && g.setEdge(n.id, t.target)));
  dagre.layout(g);
  return {
    ...graph,
    nodes: graph.nodes.map((n) => {
      const p = g.node(n.id) as { x: number; y: number; width: number; height: number } | undefined;
      return p ? { ...n, position: { x: Math.round(p.x - p.width / 2), y: Math.round(p.y - p.height / 2) } } : n;
    }),
  };
}

/** Rename a node id everywhere it is referenced. */
export function renameNode(graph: FlowGraph, from: string, to: string): FlowGraph {
  return {
    ...graph,
    initial_node: graph.initial_node === from ? to : graph.initial_node,
    nodes: graph.nodes.map((n) => ({
      ...n,
      id: n.id === from ? to : n.id,
      transitions: n.transitions.map((t) => (t.target === from ? { ...t, target: to } : t)),
      settings: n.settings.on_failure_target === from ? { ...n.settings, on_failure_target: to } : n.settings,
    })),
  };
}

export function removeNode(graph: FlowGraph, id: string): FlowGraph {
  const nodes = graph.nodes
    .filter((n) => n.id !== id)
    .map((n) => ({ ...n, transitions: n.transitions.filter((t) => t.target !== id) }));
  return { ...graph, nodes, initial_node: graph.initial_node === id ? (nodes[0]?.id ?? "") : graph.initial_node };
}

export function updateNode(graph: FlowGraph, id: string, fn: (n: FlowNode) => FlowNode): FlowGraph {
  return { ...graph, nodes: graph.nodes.map((n) => (n.id === id ? fn(n) : n)) };
}

export function transitionText(t: Transition): string {
  if (t.label) return t.label;
  if (t.kind === "expression") return t.condition || "expression";
  if (t.kind === "intent") return t.condition || "intent";
  if (t.kind === "fallback") return "fallback";
  return "";
}

export const KIND_STYLE: Record<Transition["kind"], { stroke: string; dash?: string; label: string }> = {
  on_complete: { stroke: "#71717a", label: "on complete" },
  intent: { stroke: "#7c3aed", dash: "6 4", label: "intent" },
  expression: { stroke: "#d97706", label: "expression" },
  fallback: { stroke: "#a1a1aa", dash: "2 4", label: "fallback" },
};
