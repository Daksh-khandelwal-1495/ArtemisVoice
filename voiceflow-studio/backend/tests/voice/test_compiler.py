import json
from datetime import date
from pathlib import Path

import pytest
from pipecat.flows import NO_RESPONSE, ContextStrategy, FlowsFunctionSchema

from app.schemas.flow import FlowGraph
from app.schemas.providers import BotConfig
from app.voice.compiler import FlowRuntime, RuntimeHooks, Terminal, filter_rows, render, render_obj, select_transition

TODAY = date(2026, 11, 1)
SEED_DIR = Path(__file__).resolve().parents[2] / "app" / "seed" / "bots"


class FakeHooks(RuntimeHooks):
    def __init__(self, rows=None, http=None):
        self.events, self.fields, self.nodes, self.validations, self.ended_calls = [], [], [], [], []
        self.vars_msgs, self.ends, self.transfers = [], [], []
        self.rows = rows or []
        self.http = http

    async def node_entered(self, node):
        self.nodes.append(node.id)

    async def event(self, type, node_id, payload):
        self.events.append((type, node_id, payload))

    async def field_captured(self, node_id, name, value, valid, attempts):
        self.fields.append((name, value, valid, attempts))

    async def validation(self, node_id, errors):
        self.validations.append(errors)

    async def vars_changed(self, masked, raw):
        self.vars_msgs.append(masked)

    async def lookup_rows(self, dataset, filters, mode, limit):
        return filter_rows(self.rows, filters, mode, limit)

    async def http_request(self, method, url, headers, body, timeout):
        if isinstance(self.http, Exception):
            raise self.http
        return self.http

    async def transfer(self, node_id, number):
        self.transfers.append((node_id, number))

    async def end_call(self, fm, text):
        self.ended_calls.append(text)

    async def ended(self, status, reason):
        self.ends.append((status, reason))


def node(id, type, **kw):
    return {"id": id, "type": type, "label": kw.pop("label", id.replace("_", " ").title()), **kw}


def T(target, kind="on_complete", condition="", priority=0, id=None):
    return {"id": id or f"t_{target}_{kind}_{priority}", "target": target, "kind": kind, "condition": condition, "priority": priority}


def runtime(nodes, initial=None, hooks=None, config=None, **kw):
    g = FlowGraph.model_validate({"initial_node": initial or nodes[0]["id"], "global_prompt": "You are Asha from {{brand}}.",
                                  "variables": {"brand": "Tata Motors"}, "nodes": nodes})
    return FlowRuntime(graph=g, config=config or BotConfig(), hooks=hooks or FakeHooks(), today_fn=lambda: TODAY, **kw)


def tools(cfg):
    return {f.name: f for f in cfg["functions"]}


END = node("done", "end", say_on_enter="Thanks {{name}}, goodbye!", end_reason="booked")
TRANSFER = node("agent", "transfer", say_on_enter="Connecting you now.", transfer_number="+911234")

COLLECT = node(
    "details", "collect", task_prompt="Get the caller's details.",
    fields=[
        {"name": "name", "label": "Name", "type": "string"},
        {"name": "phone", "label": "Phone", "type": "phone", "max_retries": 1, "sensitive": True},
        {"name": "email", "label": "Email", "type": "email", "required": False, "max_retries": 0},
    ],
    transitions=[T("done")],
)


# --------------------------------------------------------------------------- templating


def test_render_and_render_obj():
    v = {"name": "Ravi", "dealer": {"name": "Concorde", "city": "Pune"}, "amt": 500000.0, "ok": True}
    assert render("Hi {{name}} at {{ dealer.name }}, {{dealer.city}}", v) == "Hi Ravi at Concorde, Pune"
    assert render("{{missing}}!", v) == "!"
    assert render("{{amt}} {{ok}}", v) == "500000 yes"
    assert "{{" not in render("{{x}}", {"x": "{{evil}}"})
    assert render_obj({"a": "{{amt}}", "b": ["{{name}} x"], "c": 3}, v) == {"a": 500000.0, "b": ["Ravi x"], "c": 3}


def test_filter_rows():
    rows = [{"city": "Pune", "pin": "411001"}, {"city": "Mumbai", "pin": "400001"}, {"city": "Navi Mumbai", "pin": "400703"}]
    assert filter_rows(rows, {"city": "pune"}) == [rows[0]]
    assert filter_rows(rows, {"city": "mumbai"}, "icontains") == [rows[1], rows[2]]
    assert filter_rows(rows, {"city": None}) == []


# --------------------------------------------------------------------------- compile


async def test_start_node_compile():
    rt = runtime([
        node("start", "start", say_on_enter="Hello from {{brand}}!", settings={"respond_immediately": False},
             transitions=[T("details", "intent", "Caller wants to book a test drive"), T("done", "intent", "Not interested")]),
        COLLECT, END,
    ])
    cfg = await rt.initial()
    assert cfg["name"] == "start"
    assert "Asha from Tata Motors" in cfg["role_message"]
    assert cfg["pre_actions"] == [{"type": "tts_say", "text": "Hello from Tata Motors!"}]
    assert cfg["respond_immediately"] is False
    t = tools(cfg)
    assert set(t) == {"goto_details", "goto_done"}
    assert t["goto_details"].description == "Caller wants to book a test drive"
    assert all(isinstance(f, FlowsFunctionSchema) for f in cfg["functions"])
    assert {f.name for f in rt.global_functions()} == {"request_human_agent", "end_call"}
    assert "{{" not in cfg["task_messages"][0]["content"]


async def test_greeting_fallback_and_context_strategy():
    cfg_bot = BotConfig.model_validate({"behavior": {"greeting": "Namaste!"}})
    rt = runtime([node("start", "start", settings={"context_strategy": "reset"}, transitions=[T("done", "intent", "x")]), END], config=cfg_bot)
    cfg = await rt.initial()
    assert cfg["pre_actions"][0]["text"] == "Namaste!"
    assert cfg["context_strategy"].strategy == ContextStrategy.RESET
    rt2 = runtime([node("start", "start", settings={"context_strategy": "reset_with_summary"}, transitions=[T("done", "intent", "x")]), END])
    cfg2 = await rt2.initial()
    assert cfg2["context_strategy"].strategy == ContextStrategy.RESET_WITH_SUMMARY
    assert cfg2["context_strategy"].summary_prompt


async def test_collect_schema():
    rt = runtime([COLLECT, END])
    cfg = await rt.initial()
    t = tools(cfg)["submit_details"]
    assert set(t.properties) == {"name", "phone", "email"}
    assert t.required == ["name", "phone"]
    assert "10-digit" in t.properties["phone"]["description"]
    assert "Name" in cfg["task_messages"][0]["content"]


# --------------------------------------------------------------------------- collect semantics


async def test_collect_partial_invalid_then_complete():
    h = FakeHooks()
    rt = runtime([COLLECT, END], hooks=h)
    await rt.initial()
    submit = tools(rt.compile_node(rt.nodes["details"]))["submit_details"].handler

    res, nxt = await submit({"name": "Ravi", "phone": ""}, None)
    assert nxt is None and res["status"] == "incomplete" and res["missing"] == ["phone"]
    assert rt.vars["name"] == "Ravi"

    res, nxt = await submit({"phone": "12345"}, None)
    assert res["status"] == "invalid" and "phone" in res["errors"] and nxt is None
    assert h.validations[-1] == {"phone": res["errors"]["phone"]}

    res, nxt = await submit({"phone": "+91 98765 43210"}, None)
    assert res["status"] == "complete"
    assert isinstance(nxt, Terminal) is False  # wrap() converts terminal -> NO_RESPONSE
    assert nxt is NO_RESPONSE
    assert rt.vars["phone"] == "9876543210"
    assert h.ended_calls == ["Thanks Ravi, goodbye!"]
    assert rt.end_status == "completed" and rt.end_reason == "booked"
    # sensitive value masked in events, attempts tracked
    cap = [e for e in h.events if e[0] == "field_captured" and e[2]["field"] == "phone"][0]
    assert cap[2]["value"] == "******3210" and cap[2]["attempts"] == 2
    assert h.vars_msgs[-1]["phone"] == "******3210"


async def test_retry_exhaustion_triggers_on_failure_transfer():
    h = FakeHooks()
    rt = runtime([COLLECT, END, TRANSFER], hooks=h)
    await rt.initial()
    submit = tools(rt.compile_node(rt.nodes["details"]))["submit_details"].handler
    res, nxt = await submit({"name": "A", "phone": "1"}, None)
    assert res["status"] == "invalid"
    res, nxt = await submit({"phone": "2"}, None)  # failures 2 > max_retries 1
    assert res["status"] == "failed" and nxt is NO_RESPONSE
    assert rt.end_status == "transferred"
    assert h.transfers == [("agent", "+911234")]
    assert h.ended_calls == ["Connecting you now."]


async def test_optional_field_skipped_after_retries():
    rt = runtime([COLLECT, END])
    await rt.initial()
    submit = tools(rt.compile_node(rt.nodes["details"]))["submit_details"].handler
    res, nxt = await submit({"name": "A", "phone": "9876543210", "email": "nope"}, None)
    assert res["status"] == "complete"  # optional email over retries (0) -> skipped
    assert "email" in rt.skipped


@pytest.mark.parametrize("action,expect", [("end", "abandoned"), ("goto", "goto"), ("skip", "skip")])
async def test_on_failure_variants(action, expect):
    collect = {**COLLECT, "settings": {"on_failure": action, "on_failure_target": "fallback_conv"}}
    nodes = [collect, END, node("fallback_conv", "conversation", transitions=[T("done", "intent", "x")])]
    rt = runtime(nodes)
    await rt.initial()
    submit = tools(rt.compile_node(rt.nodes["details"]))["submit_details"].handler
    await submit({"name": "A", "phone": "1"}, None)
    res, nxt = await submit({"phone": "2"}, None)
    assert res["status"] == "failed"
    if expect == "abandoned":
        assert nxt is NO_RESPONSE and rt.end_status == "abandoned"
    elif expect == "goto":
        assert nxt["name"] == "fallback_conv"
    else:
        assert nxt is NO_RESPONSE and rt.end_status == "completed" and "phone" in rt.skipped


async def test_transfer_without_transfer_node_ends_abandoned():
    rt = runtime([COLLECT, END])
    await rt.initial()
    submit = tools(rt.compile_node(rt.nodes["details"]))["submit_details"].handler
    await submit({"name": "A", "phone": "1"}, None)
    _, nxt = await submit({"phone": "1"}, None)
    assert nxt is NO_RESPONSE and rt.end_status == "abandoned"


async def test_node_level_max_attempts_on_empty_submissions():
    collect = {**COLLECT, "settings": {"max_attempts": 2, "on_failure": "end"}}
    rt = runtime([collect, END])
    await rt.initial()
    submit = tools(rt.compile_node(rt.nodes["details"]))["submit_details"].handler
    res, _ = await submit({"name": ""}, None)
    assert res["status"] == "incomplete"
    res, nxt = await submit({}, None)
    assert res["status"] == "failed" and rt.end_status == "abandoned"


async def test_confirm_flow():
    pan = node("pan_step", "collect", fields=[{"name": "pan", "label": "PAN", "type": "pan", "confirm": True, "sensitive": True}],
               transitions=[T("done")])
    rt = runtime([pan, END])
    cfg = await rt.initial()
    t = tools(cfg)["submit_pan_step"]
    assert "confirmed" in t.properties and "confirmed" in t.required
    assert "read the value back" in cfg["task_messages"][0]["content"]
    res, nxt = await t.handler({"pan": "abcde1234f", "confirmed": False}, None)
    assert res["status"] == "needs_confirmation" and res["confirm"] == {"pan": "ABCDE1234F"} and nxt is None
    assert "pan" not in rt.vars
    # corrected value replaces pending one
    res, _ = await t.handler({"pan": "ABCDE1234G", "confirmed": False}, None)
    assert res["confirm"] == {"pan": "ABCDE1234G"}
    res, nxt = await t.handler({"confirmed": True}, None)
    assert res["status"] == "complete" and rt.vars["pan"] == "ABCDE1234G" and nxt is NO_RESPONSE


async def test_transition_selection_order():
    nodes = [
        node("ask", "collect", fields=[{"name": "amount", "label": "Amount", "type": "amount"}],
             transitions=[T("low", "expression", "amount < 100000", priority=2), T("mid", "expression", "amount < 1000000", priority=1),
                          T("other"), T("fb", "fallback")]),
        *[node(n, "conversation", transitions=[T("done", "intent", "x")]) for n in ("low", "mid", "other", "fb")], END,
    ]
    rt = runtime(nodes)
    await rt.initial()
    submit = tools(rt.compile_node(rt.nodes["ask"]))["submit_ask"].handler
    _, nxt = await submit({"amount": "50k"}, None)
    assert nxt["name"] == "mid"  # priority 1 evaluated before priority 2
    n = rt.nodes["ask"]
    assert select_transition(n, {"amount": 5_000_000}).target == "other"
    assert select_transition(n, {"amount": 5_000_000}, prefer_fallback=True).target == "fb"


async def test_intent_tools_on_collect_and_revisit():
    nodes = [
        node("ask", "collect", fields=[{"name": "amount", "label": "Amount", "type": "amount"}],
             transitions=[T("again"), T("done", "intent", "Caller not interested")]),
        node("again", "conversation", transitions=[T("ask", "intent", "Try another amount")]), END,
    ]
    rt = runtime(nodes)
    cfg = await rt.initial()
    assert set(tools(cfg)) == {"submit_ask", "goto_done"}
    _, nxt = await tools(cfg)["submit_ask"].handler({"amount": "5 lakh"}, None)
    assert nxt["name"] == "again"
    _, back = await tools(nxt)["goto_ask"].handler({}, None)
    assert back["name"] == "ask" and "amount" not in rt.vars and rt.vars["previous_amount"] == 500000


async def test_collect_skipped_when_already_captured_upstream():
    nodes = [
        node("a", "collect", fields=[{"name": "city", "label": "City"}], transitions=[T("b")]),
        node("b", "collect", fields=[{"name": "city", "label": "City"}], transitions=[T("done")]), END,
    ]
    rt = runtime(nodes)
    cfg = await rt.initial()
    _, nxt = await tools(cfg)["submit_a"].handler({"city": "Pune"}, None)
    assert nxt is NO_RESPONSE and rt.history == ["a", "b", "done"]


# --------------------------------------------------------------------------- auto nodes


async def test_decision_expression_and_fallback():
    nodes = [
        node("dec", "decision", transitions=[T("yes", "expression", "score >= 700"), T("no", "fallback")]),
        node("yes", "end", say_on_enter="Approved"), node("no", "end", say_on_enter="Rejected", end_reason="rejected"),
    ]
    rt = runtime(nodes)
    rt.vars["score"] = 720
    assert (await rt.initial()).text == "Approved"
    rt2 = runtime(nodes)
    out = await rt2.initial()
    assert out.text == "Rejected" and rt2.end_reason == "rejected"


async def test_decision_with_intent_uses_llm_router():
    nodes = [node("dec", "decision", transitions=[T("a", "intent", "wants A"), T("b", "intent", "wants B")]),
             node("a", "end"), node("b", "end")]
    rt = runtime(nodes)
    cfg = await rt.initial()
    assert set(tools(cfg)) == {"goto_a", "goto_b"} and cfg["respond_immediately"] is True


async def test_lookup_found_and_not_found():
    rows = [{"city": "Pune", "dealer": "Concorde Motors"}]
    nodes = [
        node("lk", "lookup", lookup={"dataset": "tata_dealers", "match": {"city": "city"}, "save_as": "dealer"},
             transitions=[T("found", "expression", "dealer_found == True"), T("missing", "fallback")]),
        node("found", "end", say_on_enter="Your dealer is {{dealer.dealer}}."), node("missing", "end", say_on_enter="None near you."),
    ]
    h = FakeHooks(rows=rows)
    rt = runtime(nodes, hooks=h)
    rt.vars["city"] = "pune"
    out = await rt.initial()
    assert out.text == "Your dealer is Concorde Motors." and rt.vars["dealer_found"] is True
    assert any(e[0] == "lookup" and e[2]["found"] for e in h.events)
    rt2 = runtime(nodes, hooks=FakeHooks(rows=rows))
    rt2.vars["city"] = "Delhi"
    assert (await rt2.initial()).text == "None near you." and rt2.vars["dealer"] is None


async def test_api_call_mock_and_error():
    nodes = [
        node("api", "api_call", api_call={"url": "https://x/{{id}}", "body_template": {"id": "{{id}}"},
                                          "mock_response": {"ref": "R-{{id}}"}, "save_as": "res"},
             transitions=[T("ok"), T("err", "fallback")]),
        node("ok", "end", say_on_enter="Ref {{res.ref}}"), node("err", "end", say_on_enter="Failed: {{res_error}}"),
    ]
    rt = runtime(nodes, api_call_mock=True)
    rt.vars["id"] = 7
    assert (await rt.initial()).text == "Ref R-7"
    rt2 = runtime(nodes, api_call_mock=False, hooks=FakeHooks(http=RuntimeError("boom")))
    out = await rt2.initial()
    assert out.text == "Failed: boom" and rt2.vars["res_error"] == "boom"
    rt3 = runtime(nodes, api_call_mock=False, hooks=FakeHooks(http={"ref": "LIVE"}))
    assert (await rt3.initial()).text == "Ref LIVE"


async def test_auto_loop_protection():
    nodes = [node("a", "decision", transitions=[T("b", "fallback")]), node("b", "decision", transitions=[T("a", "fallback")])]
    rt = runtime(nodes)
    out = await rt.initial()
    assert isinstance(out, Terminal) and rt.end_status == "error"


async def test_end_node_with_llm_goodbye():
    rt = runtime([node("e", "end", task_prompt="Thank the caller warmly.")])
    cfg = await rt.initial()
    assert cfg["post_actions"] == [{"type": "end_conversation"}] and rt.end_status == "completed"


# --------------------------------------------------------------------------- globals


async def test_global_tools():
    h = FakeHooks()
    rt = runtime([node("c", "conversation", transitions=[T("done", "intent", "x")]), END, TRANSFER], hooks=h)
    await rt.initial()
    g = {f.name: f for f in rt.global_functions()}
    res, nxt = await g["request_human_agent"].handler({"reason": "wants human"}, None)
    assert nxt is NO_RESPONSE and rt.end_status == "transferred"

    rt2 = runtime([node("c", "conversation", transitions=[T("done", "intent", "x")]), END], hooks=FakeHooks())
    await rt2.initial()
    g2 = {f.name: f for f in rt2.global_functions()}
    _, nxt = await g2["end_call"].handler({}, None)
    assert nxt is NO_RESPONSE and (rt2.end_status, rt2.end_reason) == ("abandoned", "user_ended")
    assert rt2.hooks.ends == [("abandoned", "user_ended")]


async def test_builtin_vars_and_interruptions():
    cfg = BotConfig.model_validate({"behavior": {"allow_interruptions": True, "idle_timeout_secs": 20}})
    rt = runtime([node("c", "conversation", settings={"allow_interruptions": False, "silence_timeout_secs": 7},
                       transitions=[T("done", "intent", "x")]), END], config=cfg, bot_name="Tata")
    await rt.initial()
    assert rt.vars["today"] == "2026-11-01" and rt.vars["bot_name"] == "Tata"
    assert rt.allows_interruptions() is False and rt.silence_timeout() == 7


# --------------------------------------------------------------------------- seed bots


def _seed_files():
    return sorted(SEED_DIR.glob("*.json"))


@pytest.mark.parametrize("path", _seed_files(), ids=lambda p: p.stem)
async def test_seed_bots_compile_every_node(path):
    data = json.loads(path.read_text())
    graph = FlowGraph.model_validate(data["graph"])
    config = BotConfig.model_validate(data.get("config") or {})
    rt = FlowRuntime(graph=graph, config=config, hooks=FakeHooks(), today_fn=lambda: TODAY, bot_name=data["name"])
    for n in graph.nodes:
        info = rt.preview(n.id)
        assert info["node_id"] == n.id
        if n.type in ("start", "collect", "conversation"):
            cfg = rt.compile_node(n)
            assert "{{" not in cfg["task_messages"][0]["content"]
            for f in cfg["functions"]:
                assert len(f.name) <= 64
                json.dumps(f.to_function_schema().to_default_dict())
    first = await rt.initial()
    assert isinstance(first, (dict, Terminal))


# --------------------------------------------------------------------------- grounding (live calls)


async def test_spoken_greeting_waits_for_caller():
    rt = runtime([node("start", "start", say_on_enter="Hi! How can I help?", transitions=[T("details", "intent", "wants to book")]),
                  COLLECT, END])
    cfg = await rt.initial()
    assert cfg["respond_immediately"] is False


async def test_grounding_rejects_values_before_caller_speaks():
    rt = runtime([COLLECT, END], require_grounding=True)
    await rt.initial()
    submit = tools(rt.compile_node(rt.nodes["details"]))["submit_details"].handler
    res, nxt = await submit({"name": "Ravi"}, None)
    assert res["status"] == "waiting_for_caller" and nxt is None
    assert "name" not in rt.vars and rt.field_failures == {}


async def test_grounding_drops_unspoken_values_but_keeps_spoken_ones():
    rt = runtime([COLLECT, END], require_grounding=True)
    rt.note_user_turn("My name is Ravi Kumar")
    await rt.initial()  # entered after the caller spoke: no new turn since entry
    submit = tools(rt.compile_node(rt.nodes["details"]))["submit_details"].handler
    res, _ = await submit({"name": "Ravi Kumar", "phone": "9876543210"}, None)
    assert rt.vars["name"] == "Ravi Kumar"
    assert "phone" not in rt.vars and res["missing"] == ["phone"]


async def test_grounding_allows_answers_given_in_this_node():
    rt = runtime([COLLECT, END], require_grounding=True)
    await rt.initial()
    rt.note_user_turn("nine eight seven six five four three two one zero")  # STT words, not digits
    submit = tools(rt.compile_node(rt.nodes["details"]))["submit_details"].handler
    res, _ = await submit({"name": "Ravi", "phone": "9876543210"}, None)
    assert rt.vars["name"] == "Ravi" and rt.vars["phone"] == "9876543210"


async def test_goto_blocked_until_caller_speaks():
    rt = runtime([node("start", "start", transitions=[T("details", "intent", "wants to book")]), COLLECT, END],
                 require_grounding=True)
    cfg = await rt.initial()
    goto = tools(cfg)["goto_details"].handler
    res, nxt = await goto({}, None)
    assert res["status"] == "waiting_for_caller" and nxt is None
    rt.note_user_turn("I want to book a test drive")
    res, nxt = await goto({}, None)
    assert nxt is not None and rt.current_node_id == "details"
