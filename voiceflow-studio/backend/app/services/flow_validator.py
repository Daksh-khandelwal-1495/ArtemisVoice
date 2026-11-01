"""Static validation of a FlowGraph (pure; no DB access).

Usage::

    from app.services.flow_validator import validate_flow
    result = validate_flow(graph, datasets={"tata_dealers"})   # FlowValidationResult

``datasets``: when provided (an iterable of dataset names), lookup nodes that reference an
unknown dataset produce an error. When None, the dataset existence check is skipped.

Issue codes (severity):
  missing_target (error)                 transition target / on_failure_target not a node
  unreachable_node (warning)             node not reachable from initial_node
  no_terminal_reachable (error)          no end/transfer node reachable via transitions
  collect_no_fields (error)
  enum_no_options (error)
  invalid_regex (error)
  duplicate_field (warning)              same field name captured in more than one node
  duplicate_field_in_node (error)        same field name twice in one node
  expression_empty (error)
  expression_invalid (error)             expression fails to parse / disallowed syntax
  expression_unknown_var (warning)
  lookup_no_dataset (error)
  lookup_unknown_dataset (error)         only when ``datasets`` is provided
  api_call_no_url (error)                no url and no mock_response
  end_has_transitions (warning)
  no_transitions (error)                 non-terminal node without transitions
  on_failure_invalid_target (error)      on_failure == goto without a valid target
  start_not_initial (error)
  multiple_start (error)
  decision_too_few_transitions (error)
  decision_invalid_transition_kind (error)
  placeholder_unknown_var (warning)      {{var}} never captured upstream
  lookup_unknown_var (warning)           lookup match var never captured upstream
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from app.schemas.flow import FlowGraph, FlowIssue, FlowNode, FlowValidationResult
from app.services.expressions import ExpressionError, parse_names

__all__ = ["validate_flow", "BUILTIN_VARS", "PLACEHOLDER_RE", "produced_vars", "placeholder_names"]

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)(?:[.\[][^}]*)?\s*\}\}")

# Variables the runtime always provides (usable in prompts/expressions without capture).
BUILTIN_VARS: frozenset[str] = frozenset(
    {"bot_name", "session_id", "today", "now", "current_date", "current_time", "caller_number"}
)

TERMINAL_TYPES = {"end", "transfer"}
DECISION_KINDS = {"expression", "intent", "fallback"}


def placeholder_names(text: str | None) -> list[str]:
    if not text:
        return []
    return PLACEHOLDER_RE.findall(text)


def produced_vars(node: FlowNode) -> set[str]:
    """Variables a node writes when it runs."""
    out: set[str] = set()
    if node.type == "collect":
        out.update(f.name for f in node.fields)
    if node.type == "lookup" and node.lookup is not None and node.lookup.save_as:
        out.update({node.lookup.save_as, f"{node.lookup.save_as}_found"})
    if node.type == "api_call":
        save_as = node.api_call.save_as if node.api_call is not None else "api_result"
        out.update({save_as, f"{save_as}_error"})
    return out


def _templated_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _templated_strings(k)
            yield from _templated_strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _templated_strings(v)


def validate_flow(
    graph: FlowGraph | dict[str, Any], datasets: Iterable[str] | None = None
) -> FlowValidationResult:
    if not isinstance(graph, FlowGraph):
        graph = FlowGraph.model_validate(graph)
    issues: list[FlowIssue] = []
    dataset_names = set(datasets) if datasets is not None else None

    def add(severity: str, code: str, message: str, node_id: str | None = None,
            field: str | None = None, transition_id: str | None = None) -> None:
        issues.append(FlowIssue(severity=severity, code=code, message=message,  # type: ignore[arg-type]
                                node_id=node_id, field=field, transition_id=transition_id))

    nodes = {n.id: n for n in graph.nodes}
    first_transfer = next((n.id for n in graph.nodes if n.type == "transfer"), None)
    first_end = next((n.id for n in graph.nodes if n.type == "end"), None)

    all_produced: set[str] = set()
    for n in graph.nodes:
        all_produced |= produced_vars(n)
    known_vars = all_produced | set(graph.variables) | BUILTIN_VARS

    # ---- start node rules
    starts = [n for n in graph.nodes if n.type == "start"]
    if len(starts) > 1:
        for n in starts[1:]:
            add("error", "multiple_start", f"Only one start node is allowed (found {len(starts)}).", n.id)
    for n in starts:
        if n.id != graph.initial_node:
            add("error", "start_not_initial", f"Start node '{n.id}' must be the initial node.", n.id)

    # ---- per-node rules
    field_owner: dict[str, str] = {}
    for n in graph.nodes:
        # transitions
        for t in n.transitions:
            if t.target not in nodes:
                add("error", "missing_target",
                    f"Transition '{t.id}' points to unknown node '{t.target}'.", n.id, transition_id=t.id)
            if t.kind == "expression":
                if not t.condition.strip():
                    add("error", "expression_empty", f"Expression transition '{t.id}' has no expression.",
                        n.id, transition_id=t.id)
                else:
                    try:
                        names = parse_names(t.condition)
                    except ExpressionError as e:
                        add("error", "expression_invalid",
                            f"Invalid expression in transition '{t.id}': {e}", n.id, transition_id=t.id)
                    else:
                        for name in sorted(names - known_vars):
                            add("warning", "expression_unknown_var",
                                f"Expression in transition '{t.id}' references unknown variable '{name}'.",
                                n.id, transition_id=t.id)

        if n.type == "end":
            if n.transitions:
                add("warning", "end_has_transitions",
                    f"End node '{n.id}' has outgoing transitions; they will be ignored.", n.id)
        elif n.type != "transfer" and not n.transitions:
            add("error", "no_transitions", f"Node '{n.id}' has no outgoing transitions.", n.id)

        # on_failure
        if n.settings.on_failure == "goto":
            tgt = n.settings.on_failure_target
            if not tgt or tgt not in nodes:
                add("error", "on_failure_invalid_target",
                    f"Node '{n.id}' uses on_failure=goto but target '{tgt or ''}' is not a valid node.", n.id)

        # collect
        if n.type == "collect" and not n.fields:
            add("error", "collect_no_fields", f"Collect node '{n.id}' has no fields to capture.", n.id)
        seen_in_node: set[str] = set()
        for f in n.fields:
            if f.name in seen_in_node:
                add("error", "duplicate_field_in_node",
                    f"Field '{f.name}' is defined twice in node '{n.id}'.", n.id, field=f.name)
            seen_in_node.add(f.name)
            if f.name in field_owner and field_owner[f.name] != n.id:
                add("warning", "duplicate_field",
                    f"Field '{f.name}' is also captured in node '{field_owner[f.name]}'; the later value overwrites.",
                    n.id, field=f.name)
            field_owner.setdefault(f.name, n.id)
            if f.type == "enum" and not (f.validation.options or []):
                add("error", "enum_no_options", f"Enum field '{f.name}' has no options.", n.id, field=f.name)
            if f.validation.regex:
                try:
                    re.compile(f.validation.regex)
                except re.error as e:
                    add("error", "invalid_regex", f"Invalid regex for field '{f.name}': {e}", n.id, field=f.name)

        # decision
        if n.type == "decision":
            if len(n.transitions) < 2:
                add("error", "decision_too_few_transitions",
                    f"Decision node '{n.id}' needs at least 2 transitions.", n.id)
            for t in n.transitions:
                if t.kind not in DECISION_KINDS:
                    add("error", "decision_invalid_transition_kind",
                        f"Decision node '{n.id}' transition '{t.id}' has kind '{t.kind}'; "
                        "only expression, intent or fallback are allowed.", n.id, transition_id=t.id)

        # lookup
        if n.type == "lookup":
            if n.lookup is None or not n.lookup.dataset.strip():
                add("error", "lookup_no_dataset", f"Lookup node '{n.id}' has no dataset configured.", n.id)
            elif dataset_names is not None and n.lookup.dataset not in dataset_names:
                add("error", "lookup_unknown_dataset",
                    f"Lookup node '{n.id}' references unknown dataset '{n.lookup.dataset}'.", n.id)

        # api_call
        if n.type == "api_call":
            cfg = n.api_call
            if cfg is None or (not (cfg.url or "").strip() and cfg.mock_response is None):
                add("error", "api_call_no_url",
                    f"API call node '{n.id}' needs a URL or a mock response.", n.id)

    # ---- reachability
    def edges(n: FlowNode, include_failure: bool) -> set[str]:
        out = {t.target for t in n.transitions if t.target in nodes} if n.type != "end" else set()
        if include_failure:
            s = n.settings
            if s.on_failure == "goto" and s.on_failure_target in nodes:
                out.add(s.on_failure_target)  # type: ignore[arg-type]
            elif s.on_failure == "transfer" and first_transfer:
                out.add(first_transfer)
            elif s.on_failure == "end" and first_end:
                out.add(first_end)
        return out

    def reach(include_failure: bool) -> set[str]:
        seen = {graph.initial_node}
        stack = [graph.initial_node]
        while stack:
            cur = stack.pop()
            for nxt in edges(nodes[cur], include_failure):
                if nxt not in seen:
                    seen.add(nxt)
                    stack.append(nxt)
        return seen

    reachable_all = reach(include_failure=True)
    # global request_human_agent / end_call tools make the first transfer/end node reachable
    for implicit in (first_transfer, first_end):
        if implicit:
            reachable_all.add(implicit)
    for n in graph.nodes:
        if n.id not in reachable_all:
            add("warning", "unreachable_node", f"Node '{n.id}' is not reachable from the initial node.", n.id)

    happy = reach(include_failure=False)
    if not any(nodes[i].type in TERMINAL_TYPES for i in happy):
        add("error", "no_terminal_reachable",
            "No end or transfer node is reachable from the initial node.", graph.initial_node)

    # ---- placeholders: vars available on entry = union over predecessors (may-analysis)
    preds: dict[str, set[str]] = {i: set() for i in nodes}
    for n in graph.nodes:
        for nxt in edges(n, include_failure=True):
            preds[nxt].add(n.id)
    base = set(graph.variables) | BUILTIN_VARS
    avail: dict[str, set[str]] = {i: set(base) for i in nodes}
    changed = True
    while changed:
        changed = False
        for n in graph.nodes:
            new = set(base)
            for p in preds[n.id]:
                new |= avail[p] | produced_vars(nodes[p])
            if new != avail[n.id]:
                avail[n.id] = new
                changed = True

    for name in sorted({*placeholder_names(graph.global_prompt)} - known_vars):
        add("warning", "placeholder_unknown_var",
            f"Global prompt references '{{{{{name}}}}}' which is never captured.")
    for n in graph.nodes:
        texts: list[str] = [n.task_prompt, n.say_on_enter or ""]
        if n.api_call is not None:
            texts += [n.api_call.url, *_templated_strings(n.api_call.headers),
                      *_templated_strings(n.api_call.body_template)]
        reported: set[str] = set()
        for text in texts:
            for name in placeholder_names(text):
                if name in avail[n.id] or name in reported:
                    continue
                reported.add(name)
                add("warning", "placeholder_unknown_var",
                    f"Node '{n.id}' references '{{{{{name}}}}}' which is never captured before this node.",
                    n.id)
        if n.lookup is not None:
            for col, var in n.lookup.match.items():
                if var not in avail[n.id]:
                    add("warning", "lookup_unknown_var",
                        f"Lookup '{n.id}' matches column '{col}' on '{var}' which is never captured before this node.",
                        n.id)

    return FlowValidationResult(valid=not any(i.severity == "error" for i in issues), issues=issues)
