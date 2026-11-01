"""Unit tests: fault functions, sandbox tools, compiler mock_api_call hook, patches, RunTrace hashing."""

from __future__ import annotations

import hashlib

import pytest

from app.replay.faults import FaultProfile, endpoint_segment, llm_swap_args, stt_transcribe, tts_speak
from app.replay.patches import PatchError, apply_patch
from app.replay.runtrace import state_hash
from app.replay.sandbox import SandboxTools, SandboxViolation, SimulatedToolError
from app.schemas.flow import FlowGraph
from app.schemas.providers import BotConfig
from app.voice.compiler import FlowRuntime, RuntimeHooks


def test_stt_keyword_protection_and_override():
    cfg = BotConfig.model_validate({"stt": {"keywords": ["Nexon"]}})
    fp = FaultProfile.parse({"stt": {"replacements": [{"from": "411001", "to": "41100I"}, {"from": "Nexon", "to": "Nixon"}],
                                     "keyword_protected": True}})
    text, applied = stt_transcribe("Nexon in Pune 411001", fp, cfg)
    assert text == "Nexon in Pune 41100I" and applied == [{"from": "411001", "to": "41100I"}]
    fp2 = FaultProfile.parse({"stt": {"replacements": [{"from": "Nexon", "to": "Nixon", "keyword_protected": False}]}})
    assert stt_transcribe("the nexon", fp2, cfg)[0] == "the Nixon"
    boosted = BotConfig.model_validate({"stt": {"keywords": ["Nexon", "411001"]}})
    assert stt_transcribe("Pune 411001", fp, boosted)[0] == "Pune 411001"
    assert fp.dump()["stt"]["replacements"][0]["from"] == "411001"


def test_endpointing_truncation_and_vad_fix():
    fp = FaultProfile.parse({"endpointing": {"truncate_field": "pincode", "keep_chars": 4}})
    cfg = BotConfig()
    assert endpoint_segment("Pune, 411001 please", fp, cfg, field_type="pincode") == ("Pune, 4110", True)
    assert endpoint_segment("Pune, 411001", fp, cfg, value_hint="411001") == ("Pune, 4110", True)
    assert endpoint_segment("just Pune", fp, cfg, field_type="pincode") == ("just Pune", False)
    slow = BotConfig.model_validate({"vad": {"stop_secs": 1.0}})
    assert endpoint_segment("Pune, 411001", fp, slow, field_type="pincode") == ("Pune, 411001", False)


def test_llm_swap_and_tts():
    fp = FaultProfile.parse({"llm": {"swap_field": {"field": "pincode", "with_value_of": "phone"}},
                             "tts": {"replacements": [{"from": "Nexon", "to": "Nixon"}]}})
    args, applied = llm_swap_args({"city": "Pune", "pincode": "411001"}, fp, vars={}, known_values={"phone": "9876543210"})
    assert args["pincode"] == "9876543210" and applied["from"] == "411001"
    assert llm_swap_args({"city": "Pune", "pincode": ""}, fp, vars={}, known_values={"phone": "1"})[1] is None
    assert tts_speak("Your Nexon is booked", fp)[0] == "Your Nixon is booked"


async def test_sandbox_tools_faults_and_oracle():
    ds = {"d": [{"city": "Pune", "pincode": "411001", "name": "A"}, {"city": "Pune", "pincode": "411045", "name": "B"}]}
    t = SandboxTools(ds)
    assert [r["name"] for r in await t.lookup("d", {"city": "pune"}, "icontains", 5)] == ["A", "B"]
    with pytest.raises(SandboxViolation):
        await t.http_request("POST", "https://example.com", {}, {}, 1)
    err = SandboxTools(ds, profile=FaultProfile.parse({"tools": {"api_error": True}}))
    with pytest.raises(SimulatedToolError):
        await err.lookup("d", {"city": "Pune"}, "exact", 5)
    with pytest.raises(SimulatedToolError):
        await err.mock_api_call("n", "POST", "", {}, {"ok": True})
    over = SandboxTools(ds, profile=FaultProfile.parse({"tools": {"lookup_override": {"rows": [{"name": "Z"}]}}}))
    assert await over.lookup("d", {"city": "Pune"}, "exact", 5) == [{"name": "Z"}]
    oracle = SandboxTools(ds, profile=FaultProfile.parse({"tools": {"api_error": True}}), tools_oracle=True,
                          expected_lookup={"dataset": "d", "match": {"city": "Pune", "pincode": "411045"}})
    assert [r["name"] for r in await oracle.lookup("d", {"city": "Pune"}, "icontains", 5)] == ["B"]
    assert await oracle.mock_api_call("n", "POST", "", {}, {"ok": True}) == {"ok": True}
    calls, results = oracle.drain()
    assert [c["kind"] for c in calls] == ["lookup", "api_call"] and results[0]["source"] == "oracle"


def _api_graph():
    return FlowGraph.model_validate({"initial_node": "api", "nodes": [
        {"id": "api", "type": "api_call", "label": "API", "api_call": {"url": "", "save_as": "res",
                                                                     "mock_response": {"ok": True}},
         "transitions": [{"id": "t1", "target": "ok", "kind": "on_complete"},
                         {"id": "t2", "target": "bad", "kind": "fallback"}]},
        {"id": "ok", "type": "end", "label": "ok", "say_on_enter": "ok"},
        {"id": "bad", "type": "end", "label": "bad", "say_on_enter": "bad"},
    ]})


async def test_compiler_mock_api_call_hook_is_additive():
    rt = FlowRuntime(graph=_api_graph(), hooks=RuntimeHooks())
    t = await rt.initial()
    assert t.node_id == "ok" and rt.vars["res"] == {"ok": True} and rt.vars["res_error"] is None

    class Failing(RuntimeHooks):
        async def mock_api_call(self, *a):
            raise RuntimeError("boom")

    rt2 = FlowRuntime(graph=_api_graph(), hooks=Failing())
    t2 = await rt2.initial()
    assert t2.node_id == "bad" and rt2.vars["res_error"] == "boom"


def test_patch_application_is_pure_and_bounded():
    import json
    from pathlib import Path

    seed = json.loads((Path(__file__).resolve().parents[2] / "app/seed/bots/tata-motors.json").read_text())
    g, c = seed["graph"], seed["config"]
    g2, c2, diff = apply_patch(g, c, {"type": "stt_keyterm_boost", "params": {"keywords": ["411001", "nexon"]}})
    assert c2["stt"]["keywords"][-1] == "411001" and c2["stt"]["keywords"].count("Nexon") == 1
    assert "411001" not in c["stt"]["keywords"] and g2 == FlowGraph.model_validate(g).model_dump(mode="json")
    _, c3, _ = apply_patch(g, c, {"type": "vad_stop_secs", "params": {"stop_secs": 1.2}})
    assert c3["vad"]["stop_secs"] == 1.2
    with pytest.raises(PatchError):
        apply_patch(g, c, {"type": "confirmation_readback", "params": {}})


def test_state_hash_matches_artemis_definition():
    st = {"b": "2", "a": "1"}
    assert state_hash(st) == hashlib.sha256(b"a=1\nb=2").hexdigest()
