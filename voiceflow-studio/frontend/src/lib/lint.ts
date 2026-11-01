// Client-side flow lint: instant feedback mirroring backend/app/services/flow_validator.py.
// The server /flow/validate result is authoritative; this only gives fast inline hints.
import { BUILTIN_VARS, SLUG_RE, type FlowGraph, type FlowIssue, type FlowNode } from "./types";

const KEYWORDS = new Set(["and", "or", "not", "in", "is", "True", "False", "None", "true", "false", "null", "len", "lower"]);

/** Extract variable names from a python-ish boolean expression; reports basic syntax problems. */
export function parseExpression(expr: string): { names: string[]; error: string | null } {
  const names = new Set<string>();
  const src = expr.trim();
  if (!src) return { names: [], error: "Expression is empty" };
  let depth = 0;
  let i = 0;
  let lastTok: "op" | "val" | null = null;
  while (i < src.length) {
    const c = src[i];
    if (/\s/.test(c)) {
      i++;
      continue;
    }
    if (c === "'" || c === '"') {
      const end = src.indexOf(c, i + 1);
      if (end < 0) return { names: [...names], error: "Unterminated string literal" };
      i = end + 1;
      lastTok = "val";
      continue;
    }
    if (c === "(" || c === "[") {
      depth++;
      i++;
      lastTok = "op";
      continue;
    }
    if (c === ")" || c === "]") {
      depth--;
      if (depth < 0) return { names: [...names], error: "Unbalanced parentheses" };
      i++;
      lastTok = "val";
      continue;
    }
    const m = /^[A-Za-z_][A-Za-z0-9_]*/.exec(src.slice(i));
    if (m) {
      const w = m[0];
      if (!KEYWORDS.has(w)) {
        // attribute access like x.lower() — only take the root
        if (src[i - 1] !== ".") names.add(w);
      }
      i += w.length;
      lastTok = ["and", "or", "not", "in", "is"].includes(w) ? "op" : "val";
      continue;
    }
    const num = /^\d+(\.\d+)?/.exec(src.slice(i));
    if (num) {
      i += num[0].length;
      lastTok = "val";
      continue;
    }
    const op = /^(==|!=|<=|>=|<|>|\+|-|\*|\/|%|,|\.)/.exec(src.slice(i));
    if (op) {
      if (op[0] === "=" ) return { names: [...names], error: "Use == for comparison" };
      i += op[0].length;
      lastTok = "op";
      continue;
    }
    if (c === "=") return { names: [...names], error: "Use == for comparison" };
    if (c === "&" || c === "|") return { names: [...names], error: "Use 'and' / 'or' instead of && / ||" };
    return { names: [...names], error: `Unexpected character '${c}'` };
  }
  if (depth !== 0) return { names: [...names], error: "Unbalanced parentheses" };
  if (lastTok === "op") return { names: [...names], error: "Expression ends with an operator" };
  return { names: [...names], error: null };
}

export function placeholders(text: string | null | undefined): string[] {
  if (!text) return [];
  return [...text.matchAll(/\{\{\s*([A-Za-z_][A-Za-z0-9_]*)(?:\.[A-Za-z0-9_.]+)?\s*\}\}/g)].map((m) => m[1]);
}

/** Variables a node produces when it runs. */
export function producedVars(n: FlowNode): string[] {
  const out = n.fields.map((f) => f.name);
  if (n.type === "lookup" && n.lookup?.save_as) out.push(n.lookup.save_as, `${n.lookup.save_as}_found`);
  if (n.type === "api_call" && n.api_call?.save_as) out.push(n.api_call.save_as, `${n.api_call.save_as}_error`);
  return out;
}

/** Node ids that can reach `nodeId` (ancestors), not including itself unless in a cycle. */
export function ancestors(graph: FlowGraph, nodeId: string): Set<string> {
  const rev = new Map<string, string[]>();
  for (const n of graph.nodes)
    for (const t of n.transitions) rev.set(t.target, [...(rev.get(t.target) ?? []), n.id]);
  const seen = new Set<string>();
  const stack = [...(rev.get(nodeId) ?? [])];
  while (stack.length) {
    const x = stack.pop()!;
    if (seen.has(x)) continue;
    seen.add(x);
    stack.push(...(rev.get(x) ?? []));
  }
  return seen;
}

/** Vars available inside a node: builtins, static variables, and everything captured upstream. */
export function availableVars(graph: FlowGraph, nodeId: string, includeSelf = true): { name: string; source: string }[] {
  const out = new Map<string, string>();
  BUILTIN_VARS.forEach((v) => out.set(v, "built-in"));
  Object.keys(graph.variables ?? {}).forEach((v) => out.set(v, "static"));
  const anc = ancestors(graph, nodeId);
  if (includeSelf) anc.add(nodeId);
  for (const n of graph.nodes) if (anc.has(n.id)) producedVars(n).forEach((v) => out.set(v, n.label || n.id));
  return [...out.entries()].map(([name, source]) => ({ name, source }));
}

function reachable(graph: FlowGraph): Set<string> {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));
  const seen = new Set<string>();
  const stack = [graph.initial_node];
  while (stack.length) {
    const id = stack.pop()!;
    if (seen.has(id) || !byId.has(id)) continue;
    seen.add(id);
    const n = byId.get(id)!;
    n.transitions.forEach((t) => stack.push(t.target));
    if (n.settings.on_failure === "goto" && n.settings.on_failure_target) stack.push(n.settings.on_failure_target);
  }
  return seen;
}

export function lintFlow(graph: FlowGraph, datasets?: string[]): FlowIssue[] {
  const issues: FlowIssue[] = [];
  const err = (code: string, message: string, extra: Partial<FlowIssue> = {}) =>
    issues.push({ severity: "error", code, message, ...extra });
  const warn = (code: string, message: string, extra: Partial<FlowIssue> = {}) =>
    issues.push({ severity: "warning", code, message, ...extra });

  const ids = new Set<string>();
  for (const n of graph.nodes) {
    if (ids.has(n.id)) err("duplicate_node_id", `Duplicate node id '${n.id}'`, { node_id: n.id });
    ids.add(n.id);
  }
  if (!ids.has(graph.initial_node)) err("missing_initial", `Initial node '${graph.initial_node}' does not exist`);

  const starts = graph.nodes.filter((n) => n.type === "start");
  if (starts.length > 1) starts.slice(1).forEach((n) => err("multiple_start", "Only one start node is allowed", { node_id: n.id }));
  starts.forEach((n) => n.id !== graph.initial_node && err("start_not_initial", "Start node must be the initial node", { node_id: n.id }));

  const reach = reachable(graph);
  const fieldOwners = new Map<string, string>();

  for (const n of graph.nodes) {
    const nid = n.id;
    if (!SLUG_RE.test(nid)) err("invalid_node_id", `Node id '${nid}' must be snake_case`, { node_id: nid });
    if (!reach.has(nid) && ids.has(graph.initial_node)) warn("unreachable_node", `'${n.label}' is unreachable from the initial node`, { node_id: nid });

    for (const t of n.transitions) {
      if (!t.target || !ids.has(t.target))
        err("missing_target", `Transition points to missing node '${t.target || "(none)"}'`, { node_id: nid, transition_id: t.id });
      if (t.kind === "intent" && !t.condition.trim())
        warn("intent_no_condition", "Intent transition needs a description of when to take it", { node_id: nid, transition_id: t.id });
      if (t.kind === "expression") {
        const p = parseExpression(t.condition);
        if (p.error) err("expression_invalid", `Expression: ${p.error}`, { node_id: nid, transition_id: t.id });
        else {
          const known = new Set(availableVars(graph, nid).map((v) => v.name));
          const unknown = p.names.filter((x) => !known.has(x));
          if (unknown.length)
            warn("expression_unknown_var", `Expression references unknown vars: ${unknown.join(", ")}`, { node_id: nid, transition_id: t.id });
        }
      }
    }

    const terminal = n.type === "end" || n.type === "transfer";
    if (n.type === "end" && n.transitions.length) warn("end_has_transitions", "End node has outgoing transitions (ignored)", { node_id: nid });
    if (!terminal && n.transitions.length === 0) err("no_transitions", `'${n.label}' has no outgoing transitions`, { node_id: nid });

    if (n.type === "collect" && n.fields.length === 0) err("collect_no_fields", "Collect node has no fields", { node_id: nid });
    for (const f of n.fields) {
      if (!SLUG_RE.test(f.name)) err("invalid_field_name", `Field name '${f.name}' must be snake_case`, { node_id: nid, field: f.name });
      if (f.type === "enum" && !(f.validation.options ?? []).length)
        err("enum_no_options", `Enum field '${f.name}' has no options`, { node_id: nid, field: f.name });
      if (f.validation.regex) {
        try {
          new RegExp(f.validation.regex);
        } catch {
          err("invalid_regex", `Invalid regex on '${f.name}'`, { node_id: nid, field: f.name });
        }
      }
      if (f.validation.min != null && f.validation.max != null && f.validation.min > f.validation.max)
        err("min_gt_max", `'${f.name}': min is greater than max`, { node_id: nid, field: f.name });
      const owner = fieldOwners.get(f.name);
      if (owner && owner !== nid)
        warn("duplicate_field", `Field '${f.name}' is also captured in '${owner}' (later overwrites)`, { node_id: nid, field: f.name });
      fieldOwners.set(f.name, nid);
    }

    if (n.type === "decision") {
      if (n.transitions.length < 2) err("decision_too_few_transitions", "Decision needs at least 2 transitions", { node_id: nid });
      n.transitions
        .filter((t) => !["expression", "intent", "fallback"].includes(t.kind))
        .forEach((t) => err("decision_invalid_transition_kind", `Decision transitions must be expression/intent/fallback (got ${t.kind})`, { node_id: nid, transition_id: t.id }));
    }
    if (n.type === "lookup") {
      if (!n.lookup?.dataset) err("lookup_no_dataset", "Lookup has no dataset", { node_id: nid });
      else if (datasets && datasets.length && !datasets.includes(n.lookup.dataset))
        err("lookup_unknown_dataset", `Unknown dataset '${n.lookup.dataset}'`, { node_id: nid });
    }
    if (n.type === "lookup" && n.lookup) {
      const known = new Set(availableVars(graph, nid, false).map((v) => v.name));
      const bad = Object.values(n.lookup.match ?? {}).filter((v) => v && !known.has(v));
      if (bad.length) warn("lookup_unknown_var", `Lookup matches on vars not captured upstream: ${bad.join(", ")}`, { node_id: nid });
    }
    if (n.type === "api_call" && !n.api_call?.url && !n.api_call?.mock_response)
      err("api_call_no_url", "API call needs a URL or a mock response", { node_id: nid });
    if (n.settings.on_failure === "goto" && (!n.settings.on_failure_target || !ids.has(n.settings.on_failure_target)))
      err("on_failure_invalid_target", "on_failure = goto needs a valid target", { node_id: nid });

    const known = new Set(availableVars(graph, nid).map((v) => v.name));
    const used = [...placeholders(n.task_prompt), ...placeholders(n.say_on_enter)];
    const unknown = [...new Set(used.filter((v) => !known.has(v)))];
    if (unknown.length) warn("placeholder_unknown_var", `Uses {{${unknown.join("}}, {{")}}} not captured upstream`, { node_id: nid });
  }

  const terminals = graph.nodes.filter((n) => (n.type === "end" || n.type === "transfer") && reach.has(n.id));
  if (graph.nodes.length && !terminals.length) err("no_terminal_reachable", "No end or transfer node is reachable", { node_id: graph.initial_node });

  return issues;
}
