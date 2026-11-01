"""Test-oracle outcome check (INTEGRATION_CONTRACT §2).

``passed = expected_fields ⊆ final captured (normalised) ∧ end_node/status match ∧ expected
lookup row found ∧ final confirmation spoken_text contains every expected_bot_response.contains``

The "final confirmation" is the bot's last spoken turn (the text spoken in response to the
last caller turn) - the same text Artemis reads from the RunTrace's last TTS layer.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.schemas.flow import CaptureField, FlowGraph
from app.voice.compiler import filter_rows
from app.voice.validators import validate_field

END_NODE_KEY = "__end_node"
STATUS_KEY = "__status"


def norm_text(value: Any) -> str:
    return " ".join(str(value if value is not None else "").casefold().split())


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def graph_fields(graph: FlowGraph) -> dict[str, CaptureField]:
    out: dict[str, CaptureField] = {}
    for n in graph.nodes:
        for f in n.fields:
            out.setdefault(f.name, f)
    return out


def canonical_value(field: CaptureField | None, value: Any, today: date | None = None) -> str:
    """Normalised comparison form: the value the runtime would store, as casefolded text."""
    if field is not None and value is not None and field.type != "string":
        res = validate_field(field, value, today=today)
        if res.ok:
            value = res.value
    return norm_text(as_text(value))


def captured_fields(graph: FlowGraph, vars: dict[str, Any], captured: set[str] | None = None) -> dict[str, Any]:
    """Captured flow fields (graph fields present in vars; restricted to ``captured`` if given)."""
    names = graph_fields(graph)
    out = {}
    for name in names:
        if captured is not None and name not in captured:
            continue
        v = vars.get(name)
        if v is not None and v != "":
            out[name] = v
    return out


def database_state(graph: FlowGraph, vars: dict[str, Any], *, end_node: str | None, status: str | None,
                   captured: set[str] | None = None) -> dict[str, str]:
    """Artemis ``database_state``: captured fields + ``__end_node`` + ``__status`` as strings."""
    state = {k: as_text(v) for k, v in captured_fields(graph, vars, captured).items()}
    state[END_NODE_KEY] = end_node or ""
    state[STATUS_KEY] = status or ""
    return state


def lookup_results(graph: FlowGraph, vars: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for n in graph.nodes:
        if n.type == "lookup" and n.lookup is not None and n.lookup.save_as in vars:
            v = vars.get(n.lookup.save_as)
            rows = v if isinstance(v, list) else ([v] if isinstance(v, dict) else [])
            out.append({"node_id": n.id, "dataset": n.lookup.dataset, "rows": rows})
    return out


def expected_state(oracle: dict[str, Any], graph: FlowGraph | None = None, today: date | None = None) -> dict[str, str]:
    """Oracle expectations in ``database_state`` form (canonicalised through the field validators)."""
    fields = graph_fields(graph) if graph is not None else {}
    out: dict[str, str] = {}
    for k, v in (oracle.get("expected_fields") or {}).items():
        f = fields.get(k)
        if f is not None and f.type != "string":
            res = validate_field(f, v, today=today)
            out[k] = as_text(res.value) if res.ok else as_text(v)
        else:
            out[k] = as_text(v)
    if oracle.get("expected_end_node"):
        out[END_NODE_KEY] = str(oracle["expected_end_node"])
    if oracle.get("expected_status"):
        out[STATUS_KEY] = str(oracle["expected_status"])
    return out


def expected_contains(oracle: dict[str, Any]) -> list[str]:
    resp = oracle.get("expected_bot_response") or {}
    return [str(x) for x in (resp.get("contains") or [])] if isinstance(resp, dict) else []


def check_outcome(oracle: dict[str, Any], outcome: dict[str, Any], graph: FlowGraph,
                  today: date | None = None) -> dict[str, Any]:
    """Returns ``{passed, checks: {...}}`` with a per-criterion breakdown."""
    fields = graph_fields(graph)
    captured = outcome.get("captured") or {}
    field_checks = {}
    for name, exp in (oracle.get("expected_fields") or {}).items():
        actual = captured.get(name)
        ok = actual is not None and canonical_value(fields.get(name), exp, today) == canonical_value(fields.get(name), actual, today)
        field_checks[name] = {"expected": exp, "actual": actual, "ok": ok}

    checks: dict[str, Any] = {"fields": field_checks}
    exp_node = oracle.get("expected_end_node")
    checks["end_node"] = {"expected": exp_node, "actual": outcome.get("end_node"),
                          "ok": not exp_node or exp_node == outcome.get("end_node")}
    exp_status = oracle.get("expected_status")
    checks["status"] = {"expected": exp_status, "actual": outcome.get("status"),
                        "ok": not exp_status or exp_status == outcome.get("status")}

    el = oracle.get("expected_lookup")
    if el and el.get("dataset"):
        found = False
        for lk in outcome.get("lookups") or []:
            if lk.get("dataset") == el["dataset"] and filter_rows(lk.get("rows") or [], el.get("match") or {}, "exact", 1):
                found = True
                break
        checks["lookup"] = {"expected": el, "ok": found}
    else:
        checks["lookup"] = {"expected": None, "ok": True}

    contains = expected_contains(oracle)
    spoken = norm_text(outcome.get("final_spoken_text") or "")
    missing = [c for c in contains if norm_text(c) not in spoken]
    checks["bot_response"] = {"expected_contains": contains, "final_spoken_text": outcome.get("final_spoken_text"),
                              "missing": missing, "ok": not missing}

    passed = all(c["ok"] for c in field_checks.values()) and all(
        checks[k]["ok"] for k in ("end_node", "status", "lookup", "bot_response"))
    return {"passed": passed, "checks": checks}


def oracle_dict(o: Any) -> dict[str, Any]:
    """Serialize a TestOracle row (or pass a dict through)."""
    if isinstance(o, dict):
        return o
    return {
        "id": str(o.id),
        "bot_id": str(o.bot_id),
        "slug": o.slug,
        "name": o.name,
        "description": o.description or "",
        "caller_turns": list(o.caller_turns or []),
        "expected_fields": dict(o.expected_fields or {}),
        "expected_lookup": o.expected_lookup,
        "expected_end_node": o.expected_end_node,
        "expected_status": o.expected_status,
        "expected_bot_response": o.expected_bot_response,
        "fault_profile": o.fault_profile,
        "tags": list(o.tags or []),
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "updated_at": o.updated_at.isoformat() if o.updated_at else None,
    }
