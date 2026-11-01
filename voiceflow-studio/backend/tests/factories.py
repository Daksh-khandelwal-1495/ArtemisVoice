"""Graph builders for tests."""

from __future__ import annotations

import copy
from typing import Any


def valid_graph() -> dict[str, Any]:
    return copy.deepcopy({
        "initial_node": "start",
        "global_prompt": "You are a helpful assistant.",
        "nodes": [
            {"id": "start", "type": "start", "label": "Start", "task_prompt": "Greet.",
             "transitions": [{"id": "t1", "target": "collect_info", "kind": "on_complete"}]},
            {"id": "collect_info", "type": "collect", "label": "Collect",
             "task_prompt": "Collect the caller's details.",
             "fields": [
                 {"name": "name", "label": "Name"},
                 {"name": "phone", "label": "Phone", "type": "phone", "sensitive": True},
                 {"name": "city", "label": "City", "type": "enum",
                  "validation": {"options": ["Mumbai", "Pune"]}},
             ],
             "transitions": [{"id": "t2", "target": "find_dealer", "kind": "on_complete"}]},
            {"id": "find_dealer", "type": "lookup", "label": "Lookup",
             "lookup": {"dataset": "dealers", "match": {"city": "city"}, "save_as": "dealer"},
             "transitions": [
                 {"id": "t3", "target": "check", "kind": "on_complete"},
             ]},
            {"id": "check", "type": "decision", "label": "Check",
             "transitions": [
                 {"id": "t4", "target": "done", "kind": "expression", "condition": "dealer_found"},
                 {"id": "t5", "target": "human", "kind": "fallback"},
             ]},
            {"id": "human", "type": "transfer", "label": "Transfer", "say_on_enter": "Transferring {{name}}."},
            {"id": "done", "type": "end", "label": "End", "say_on_enter": "Thanks {{name}}, see you at {{dealer.name}}.",
             "end_reason": "completed"},
        ],
    })


def node(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    return next(n for n in graph["nodes"] if n["id"] == node_id)
