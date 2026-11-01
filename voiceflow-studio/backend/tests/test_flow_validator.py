from app.schemas.flow import FlowGraph
from app.services.flow_validator import validate_flow

from .factories import node, valid_graph

DS = {"dealers"}


def codes(graph, datasets=DS):
    r = validate_flow(FlowGraph.model_validate(graph), datasets=datasets)
    return r, {(i.severity, i.code) for i in r.issues}


def issue(r, code):
    return next(i for i in r.issues if i.code == code)


def test_valid_graph_has_no_issues():
    r, c = codes(valid_graph())
    assert r.valid and not r.issues, r.issues


def test_accepts_dict_input():
    assert validate_flow(valid_graph(), DS).valid


def test_missing_target():
    g = valid_graph()
    node(g, "start")["transitions"][0]["target"] = "nope"
    r, c = codes(g)
    assert ("error", "missing_target") in c and not r.valid
    i = issue(r, "missing_target")
    assert i.node_id == "start" and i.transition_id == "t1"


def test_unreachable_node_warning():
    g = valid_graph()
    g["nodes"].append({"id": "orphan", "type": "conversation", "label": "O",
                       "transitions": [{"id": "tx", "target": "done"}]})
    r, c = codes(g)
    assert ("warning", "unreachable_node") in c and r.valid
    assert issue(r, "unreachable_node").node_id == "orphan"


def test_no_terminal_reachable():
    g = {"initial_node": "start", "nodes": [
        {"id": "start", "type": "start", "label": "S", "transitions": [{"id": "a", "target": "loop"}]},
        {"id": "loop", "type": "conversation", "label": "L", "transitions": [{"id": "b", "target": "start"}]},
    ]}
    _, c = codes(g)
    assert ("error", "no_terminal_reachable") in c


def test_collect_no_fields():
    g = valid_graph()
    node(g, "collect_info")["fields"] = []
    r, c = codes(g)
    assert ("error", "collect_no_fields") in c
    assert issue(r, "collect_no_fields").node_id == "collect_info"


def test_enum_without_options():
    g = valid_graph()
    node(g, "collect_info")["fields"][2]["validation"]["options"] = []
    r, c = codes(g)
    assert ("error", "enum_no_options") in c
    i = issue(r, "enum_no_options")
    assert i.field == "city" and i.node_id == "collect_info"


def test_invalid_regex():
    g = valid_graph()
    node(g, "collect_info")["fields"][0]["validation"] = {"regex": "([a-z"}
    r, c = codes(g)
    assert ("error", "invalid_regex") in c and issue(r, "invalid_regex").field == "name"


def test_duplicate_field_across_nodes_warning():
    g = valid_graph()
    node(g, "start")["type"] = "start"
    g["nodes"].insert(2, {"id": "collect_again", "type": "collect", "label": "Again",
                          "fields": [{"name": "name", "label": "Name"}],
                          "transitions": [{"id": "tz", "target": "find_dealer"}]})
    node(g, "collect_info")["transitions"][0]["target"] = "collect_again"
    r, c = codes(g)
    assert ("warning", "duplicate_field") in c and r.valid
    i = issue(r, "duplicate_field")
    assert i.field == "name" and i.node_id == "collect_again"


def test_expression_invalid_and_unknown_var():
    g = valid_graph()
    node(g, "check")["transitions"][0]["condition"] = "dealer_found and ("
    r, c = codes(g)
    assert ("error", "expression_invalid") in c
    assert issue(r, "expression_invalid").transition_id == "t4"

    g = valid_graph()
    node(g, "check")["transitions"][0]["condition"] = "__import__('os')"
    _, c = codes(g)
    assert ("error", "expression_invalid") in c

    g = valid_graph()
    node(g, "check")["transitions"][0]["condition"] = "dealer_found and credit_score > 700"
    r, c = codes(g)
    assert ("warning", "expression_unknown_var") in c and r.valid
    assert "credit_score" in issue(r, "expression_unknown_var").message

    g = valid_graph()
    node(g, "check")["transitions"][0]["condition"] = "  "
    _, c = codes(g)
    assert ("error", "expression_empty") in c


def test_lookup_dataset_rules():
    g = valid_graph()
    node(g, "find_dealer")["lookup"] = None
    _, c = codes(g)
    assert ("error", "lookup_no_dataset") in c

    g = valid_graph()
    node(g, "find_dealer")["lookup"]["dataset"] = "unknown_ds"
    _, c = codes(g)
    assert ("error", "lookup_unknown_dataset") in c
    _, c = codes(g, datasets=None)  # no dataset list -> existence not checked
    assert ("error", "lookup_unknown_dataset") not in c


def test_api_call_without_url_or_mock():
    g = valid_graph()
    g["nodes"].append({"id": "api", "type": "api_call", "label": "API",
                       "api_call": {"url": "", "save_as": "res"},
                       "transitions": [{"id": "ta", "target": "done"}]})
    node(g, "check")["transitions"][1]["target"] = "api"
    r, c = codes(g)
    assert ("error", "api_call_no_url") in c and issue(r, "api_call_no_url").node_id == "api"
    node(g, "api")["api_call"]["mock_response"] = {"ok": True}
    _, c = codes(g)
    assert ("error", "api_call_no_url") not in c


def test_end_with_transitions_warning():
    g = valid_graph()
    node(g, "done")["transitions"] = [{"id": "te", "target": "start"}]
    r, c = codes(g)
    assert ("warning", "end_has_transitions") in c and r.valid


def test_non_end_without_transitions():
    g = valid_graph()
    node(g, "find_dealer")["transitions"] = []
    r, c = codes(g)
    assert ("error", "no_transitions") in c
    assert issue(r, "no_transitions").node_id == "find_dealer"


def test_on_failure_goto_without_valid_target():
    g = valid_graph()
    node(g, "collect_info")["settings"] = {"on_failure": "goto"}
    _, c = codes(g)
    assert ("error", "on_failure_invalid_target") in c
    node(g, "collect_info")["settings"] = {"on_failure": "goto", "on_failure_target": "ghost"}
    _, c = codes(g)
    assert ("error", "on_failure_invalid_target") in c
    node(g, "collect_info")["settings"] = {"on_failure": "goto", "on_failure_target": "human"}
    _, c = codes(g)
    assert ("error", "on_failure_invalid_target") not in c


def test_start_rules():
    g = valid_graph()
    g["nodes"].append({"id": "start2", "type": "start", "label": "S2",
                       "transitions": [{"id": "s2", "target": "done"}]})
    _, c = codes(g)
    assert ("error", "multiple_start") in c and ("error", "start_not_initial") in c


def test_decision_rules():
    g = valid_graph()
    node(g, "check")["transitions"] = [{"id": "only", "target": "done", "kind": "fallback"}]
    _, c = codes(g)
    assert ("error", "decision_too_few_transitions") in c
    g = valid_graph()
    node(g, "check")["transitions"][1]["kind"] = "on_complete"
    r, c = codes(g)
    assert ("error", "decision_invalid_transition_kind") in c
    assert issue(r, "decision_invalid_transition_kind").transition_id == "t5"


def test_placeholder_unknown_var():
    g = valid_graph()
    node(g, "start")["say_on_enter"] = "Hi {{name}}!"  # name captured downstream only
    r, c = codes(g)
    assert ("warning", "placeholder_unknown_var") in c and r.valid
    assert issue(r, "placeholder_unknown_var").node_id == "start"

    g = valid_graph()
    g["variables"] = {"brand": "Tata"}
    node(g, "start")["say_on_enter"] = "Welcome to {{ brand }}"
    _, c = codes(g)
    assert ("warning", "placeholder_unknown_var") not in c


def test_seed_bots_validate_clean():
    import json
    from pathlib import Path

    seed = Path(__file__).resolve().parents[1] / "app" / "seed"
    ds = {json.loads(p.read_text())["name"] for p in (seed / "datasets").glob("*.json")}
    for p in (seed / "bots").glob("*.json"):
        r = validate_flow(json.loads(p.read_text())["graph"], ds)
        assert r.valid, (p.name, [i for i in r.issues if i.severity == "error"])
