"""Replay sandbox, oracles, fault attribution, RunTrace, determinism and patches (contract §2-§3)."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ARMS = ["control", "stt_oracle", "endpointing_oracle", "llm_oracle", "tools_oracle", "tts_oracle"]
VARIANT_ARM = {
    "tata-stt": "stt_oracle",
    "tata-endpointing": "endpointing_oracle",
    "tata-llm": "llm_oracle",
    "tata-tools": "tools_oracle",
    "tata-tts": "tts_oracle",
}

_ARTEMIS = Path(__file__).resolve().parents[4] / "backend"


def artemis_run_trace():
    if str(_ARTEMIS) not in sys.path:
        sys.path.insert(0, str(_ARTEMIS))
    from artemisVoice.Evals.tracing.contracts import RunTrace

    return RunTrace


async def _simulate(c, bot, oracle, **extra):
    r = await c.post(f"/api/bots/{bot['id']}/simulate", json={"oracle_id": oracle["id"], **extra})
    assert r.status_code == 200, r.text
    return r.json()


async def _replay(c, sid, arm, **extra):
    r = await c.post(f"/api/sessions/{sid}/replay", json={"intervention": arm, **extra})
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- happy path + trace


async def test_happy_simulation_passes_and_trace_is_replay_grade(tata):
    c, bot, o = tata["client"], tata["bot"], tata["oracles"]
    sim = await _simulate(c, bot, o["tata-happy"])
    assert sim["passed"] is True
    assert sim["outcome"]["end_node"] == "end_booked" and sim["outcome"]["status"] == "completed"
    assert "Nexon" in sim["outcome"]["final_spoken_text"]
    assert sim["trace_url"] == f"/api/sessions/{sim['session_id']}/trace"

    trace = (await c.get(sim["trace_url"])).json()
    assert trace["source"] == "simulation"
    assert trace["oracle_id"] == o["tata-happy"]["id"]
    assert trace["seed"] == 7 and trace["dataset_snapshot_id"] == sim["dataset_snapshot_id"]
    assert trace["replay_ready"] is True, trace["missing"]
    assert len(trace["turns"]) == len(o["tata-happy"]["caller_turns"])
    t1 = trace["turns"][1]["layers"]
    assert t1["caller"]["output"]["intended_text"] == "I am in Pune, pincode 411001."
    art = t1["caller"]["output"]["audio"]
    assert art["media_type"].startswith("text/plain")
    assert art["sha256"] == hashlib.sha256(b"I am in Pune, pincode 411001.").hexdigest()
    assert t1["llm"]["output"]["tool_name"] == "submit_collect_location"
    assert t1["llm"]["settings"]["temperature"] == 0 and t1["llm"]["settings"]["seed"] == 7
    assert t1["tools"]["input"]["calls"][0]["kind"] == "lookup"
    assert t1["tools"]["output"]["database_state"]["pincode"] == "411001"

    # Artemis can resolve the bound oracle; simulation sessions are listed, replays are not
    assert (await c.get(f"/api/oracles/{trace['oracle_id']}")).json()["slug"] == "tata-happy"
    outcome = (await c.get(f"/api/sessions/{sim['session_id']}/outcome")).json()
    assert outcome["passed"] is True


async def test_run_trace_parses_as_artemis_runtrace(tata):
    RunTrace = artemis_run_trace()
    c, bot, o = tata["client"], tata["bot"], tata["oracles"]
    sim = await _simulate(c, bot, o["tata-happy"])
    run = RunTrace.model_validate(sim["run_trace"])
    assert run.source.value == "voiceflow" and run.studio_session_id == sim["session_id"]
    assert run.expected_spoken_contains == ["Nexon"]
    assert run.is_successful() is True
    assert run.final_database_state["__end_node"] == "end_booked"
    assert run.final_database_state["__status"] == "completed"
    for turn in run.turns:
        assert len(turn.layers) == 6 and turn.artifacts

    bad = await _simulate(c, bot, o["tata-tts"])
    rep = await _replay(c, bad["session_id"], "control", trials=2)
    for trial in rep["trials"]:
        rt = RunTrace.model_validate(trial["run_trace"])
        assert rt.is_successful() is trial["passed"] is False
    fixed = await _replay(c, bad["session_id"], "tts_oracle")
    assert RunTrace.model_validate(fixed["trials"][0]["run_trace"]).is_successful() is True


# --------------------------------------------------------------------------- attribution matrix


@pytest.mark.parametrize("variant", list(VARIANT_ARM))
async def test_fault_variant_is_attributed_to_its_layer(tata, variant):
    c, bot, o = tata["client"], tata["bot"], tata["oracles"]
    sim = await _simulate(c, bot, o[variant])
    assert sim["passed"] is False, f"{variant}: the faulty system must fail"
    assert sim["fault_profile"] == o[variant]["fault_profile"] or sim["fault_profile"]
    results = {}
    for arm in ARMS:
        rep = await _replay(c, sim["session_id"], arm)
        assert rep["intervention"] == arm and len(rep["trials"]) == 1
        results[arm] = rep["trials"][0]["passed"]
    expected = {arm: arm == VARIANT_ARM[variant] for arm in ARMS}
    assert results == expected, f"{variant}: {results}"


async def test_happy_path_passes_every_arm(tata):
    c, bot, o = tata["client"], tata["bot"], tata["oracles"]
    sim = await _simulate(c, bot, o["tata-happy"])
    for arm in ARMS:
        assert (await _replay(c, sim["session_id"], arm))["trials"][0]["passed"] is True, arm


async def test_stt_vs_endpointing_segment_semantics(tata):
    """stt_oracle fixes only what STT got wrong in the delivered segment; endpointing_oracle
    feeds STT(full utterance) so recorded STT confusions are kept."""
    c, bot, o = tata["client"], tata["bot"], tata["oracles"]
    ep = await _simulate(c, bot, o["tata-endpointing"])
    trace = (await c.get(f"/api/sessions/{ep['session_id']}/trace")).json()
    seg = trace["turns"][1]["layers"]["endpointing"]["output"]
    assert seg["segment_text"] == "I am in Pune, pincode 4110" and seg["truncated"] is True
    stt_arm = (await _replay(c, ep["session_id"], "stt_oracle"))["trials"][0]
    rtrace = (await c.get(f"/api/sessions/{stt_arm['replay_session_id']}/trace?reveal=true")).json()
    assert rtrace["turns"][1]["layers"]["speech_to_text"]["output"]["final_hypothesis"] == "I am in Pune, pincode 4110"

    stt = await _simulate(c, bot, o["tata-stt"])
    trace = (await c.get(f"/api/sessions/{stt['session_id']}/trace")).json()
    # 411001 -> 41100I fires; Nexon -> Nixon is keyword-protected (Nexon is a configured keyterm)
    assert trace["turns"][1]["layers"]["speech_to_text"]["output"]["final_hypothesis"] == "I am in Pune, pincode 41100I."
    assert "Nexon" in trace["turns"][0]["layers"]["speech_to_text"]["output"]["final_hypothesis"]
    ep_arm = (await _replay(c, stt["session_id"], "endpointing_oracle"))["trials"][0]
    rtrace = (await c.get(f"/api/sessions/{ep_arm['replay_session_id']}/trace")).json()
    assert "41100I" in rtrace["turns"][1]["layers"]["speech_to_text"]["output"]["final_hypothesis"]


# --------------------------------------------------------------------------- determinism


def _fingerprint(trial):
    o = dict(trial["outcome"])
    hashes = [t["layers"][3]["input"]["context_hash"] for t in trial["run_trace"]["turns"]]
    states = [t["database_hash_after"] for t in trial["run_trace"]["turns"]]
    return (o["captured"], o["end_node"], o["status"], o["spoken_texts"], hashes, states, trial["passed"])


async def test_same_seed_and_config_reproduce_the_baseline(tata):
    c, bot, o = tata["client"], tata["bot"], tata["oracles"]
    for slug in ("tata-happy", "tata-stt"):
        sim = await _simulate(c, bot, o[slug], seed=11)
        a = await _replay(c, sim["session_id"], "control", trials=3)
        b = await _replay(c, sim["session_id"], "control", trials=2, seed=11)
        prints = [_fingerprint(t) for t in a["trials"] + b["trials"]]
        assert all(p == prints[0] for p in prints), slug
        assert a["seed"] == b["seed"] == 11
        # the replayed baseline equals the recorded simulation outcome
        assert prints[0][0] == sim["outcome"]["captured"] and prints[0][1] == sim["outcome"]["end_node"]
        assert prints[0][6] == sim["passed"]
        assert [t["layers"][3]["input"]["context_hash"] for t in sim["run_trace"]["turns"]] == prints[0][4]


# --------------------------------------------------------------------------- sandbox


async def test_replay_sandbox_has_no_side_effects(tata, monkeypatch):
    import httpx
    from sqlalchemy import event

    import app.voice.bot as live_bot
    from app.db.models import DatasetRow
    from app.db.session import SessionLocal
    from app.replay.sandbox import collect_datasets, datasets_hash

    c, bot, o = tata["client"], tata["bot"], tata["oracles"]

    def no_network(*a, **kw):
        raise AssertionError("external network call during simulation/replay")

    async def no_live_lookup(*a, **kw):
        raise AssertionError("live dataset lookup during simulation/replay")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    monkeypatch.setattr(live_bot, "lookup_dataset_rows", no_live_lookup)
    writes = []

    def on_write(*a):
        writes.append(a)

    for ev in ("before_insert", "before_update", "before_delete"):
        event.listen(DatasetRow, ev, on_write)
    try:
        async with SessionLocal() as s:
            before = datasets_hash(await collect_datasets(s))
        sim = await _simulate(c, bot, o["tata-tools"])
        for arm in ARMS:
            await _replay(c, sim["session_id"], arm)
        async with SessionLocal() as s:
            after = datasets_hash(await collect_datasets(s))
    finally:
        for ev in ("before_insert", "before_update", "before_delete"):
            event.remove(DatasetRow, ev, on_write)
    assert before == after and not writes


async def test_replay_reads_the_snapshot_not_live_tables(tata):
    c, bot, o = tata["client"], tata["bot"], tata["oracles"]
    sim = await _simulate(c, bot, o["tata-happy"])
    rows = (await c.get("/api/datasets/tata_dealers/rows", params={"q": "411001"})).json()
    items = rows["items"] if isinstance(rows, dict) else rows
    camp = next(r for r in items if (r.get("data") or r).get("pincode") == "411001")
    assert (await c.delete(f"/api/datasets/tata_dealers/rows/{camp['id']}")).status_code in (200, 204)
    rep = await _replay(c, sim["session_id"], "control")
    assert rep["trials"][0]["passed"] is True  # the frozen snapshot still has the dealer
    assert rep["dataset_snapshot_id"] == sim["dataset_snapshot_id"]
    snap = (await c.get(f"/api/dataset-snapshots/{sim['dataset_snapshot_id']}")).json()
    assert any(r["pincode"] == "411001" for r in snap["datasets"]["tata_dealers"]["rows"])
    # a new simulation sees the live change (dealer gone -> lookup check fails)
    sim2 = await _simulate(c, bot, o["tata-happy"])
    assert sim2["dataset_snapshot_id"] != sim["dataset_snapshot_id"] and sim2["passed"] is False


async def test_replay_sessions_are_hidden_from_default_lists(tata):
    c, bot, o = tata["client"], tata["bot"], tata["oracles"]
    sim = await _simulate(c, bot, o["tata-happy"])
    rep = await _replay(c, sim["session_id"], "control", trials=2)
    rids = {t["replay_session_id"] for t in rep["trials"]}
    default = {i["id"]: i for i in (await c.get("/api/sessions")).json()["items"]}
    assert sim["session_id"] in default and default[sim["session_id"]]["mode"] == "simulation"
    assert not rids & set(default)
    full = {i["id"]: i for i in (await c.get("/api/sessions", params={"include_replay": "true"})).json()["items"]}
    assert rids <= set(full) and all(full[r]["mode"] == "replay" for r in rids)
    rtrace = (await c.get(f"/api/sessions/{next(iter(rids))}/trace")).json()
    assert rtrace["source"] == "replay" and rtrace["session"]["mode"] == "replay"


async def test_replay_validation_errors(tata):
    c, bot, o = tata["client"], tata["bot"], tata["oracles"]
    sim = await _simulate(c, bot, o["tata-happy"])
    assert (await c.post(f"/api/sessions/{sim['session_id']}/replay", json={"intervention": "nope"})).status_code == 422
    assert (await c.post(f"/api/sessions/{sim['session_id']}/replay", json={"trials": 11})).status_code == 422
    assert (await c.post("/api/sessions/00000000-0000-0000-0000-000000000000/replay", json={})).status_code == 404


async def test_missing_openai_key_is_a_503(client, seeded, monkeypatch):
    from app.replay import llm as llm_mod
    from app.voice.providers import ProviderKeys

    monkeypatch.setattr(ProviderKeys, "from_settings", classmethod(lambda cls: cls()))
    llm_mod.set_llm_factory(None)
    bot = (await client.get("/api/bots/tata-motors")).json()
    oracle = (await client.get(f"/api/bots/{bot['id']}/oracles")).json()[0]
    r = await client.post(f"/api/bots/{bot['id']}/simulate", json={"oracle_id": oracle["id"]})
    assert r.status_code == 503 and "OPENAI_API_KEY" in r.text


# --------------------------------------------------------------------------- oracles


async def test_oracle_crud_tags_and_binding(tata):
    c, bot, o = tata["client"], tata["bot"], tata["oracles"]
    assert set(VARIANT_ARM) | {"tata-happy"} <= set(o)
    body = {"name": "Regression: Pune callback", "caller_turns": ["hi"], "expected_fields": {"city": "Pune"},
            "expected_end_node": "end_callback", "tags": ["regression", "held_out"],
            "expected_bot_response": {"contains": ["callback"]}}
    r = await c.post(f"/api/bots/{bot['id']}/oracles", json=body)
    assert r.status_code == 201
    new = r.json()
    assert new["slug"] == "regression-pune-callback" and new["tags"] == ["held_out", "regression"]
    assert (await c.post(f"/api/bots/{bot['id']}/oracles", json={**body, "slug": "tata-happy"})).status_code == 409
    reg = (await c.get(f"/api/bots/{bot['id']}/oracles", params={"tag": "regression"})).json()
    assert {x["slug"] for x in reg} == {"tata-happy", "regression-pune-callback"}
    held = (await c.get(f"/api/bots/{bot['id']}/oracles", params={"tag": "held_out"})).json()
    assert [x["slug"] for x in held] == ["regression-pune-callback"]
    p = await c.patch(f"/api/oracles/{new['id']}", json={"description": "d", "fault_profile": {"tts": {"replacements": [{"from": "a", "to": "b"}]}}})
    assert p.status_code == 200 and p.json()["description"] == "d" and p.json()["fault_profile"]["tts"]
    assert (await c.patch(f"/api/oracles/{new['id']}", json={"fault_profile": {"stt": {"replacements": "x"}}})).status_code == 422

    sim = await _simulate(c, bot, o["tata-happy"])
    b = await c.post(f"/api/sessions/{sim['session_id']}/oracle", json={"oracle_id": new["id"], "turn_truths": {"0": "hello"}})
    assert b.status_code == 200 and b.json()["turn_truths"]["0"] == "hello"
    out = (await c.get(f"/api/sessions/{sim['session_id']}/outcome")).json()
    assert out["oracle_id"] == new["id"] and out["passed"] is False
    assert (await c.delete(f"/api/oracles/{new['id']}")).status_code == 204
    assert (await c.get(f"/api/oracles/{new['id']}")).status_code == 404


# --------------------------------------------------------------------------- patches


async def test_keyterm_patch_candidate_fixes_stt_and_needs_approval(tata):
    c, bot, o = tata["client"], tata["bot"], tata["oracles"]
    published_before = bot["published_version_id"]
    patch = {"type": "stt_keyterm_boost", "params": {"keywords": ["411001"]}}
    r = await c.post(f"/api/bots/{bot['id']}/patch-candidates", json={"patch": patch})
    assert r.status_code == 201, r.text
    cand = r.json()
    assert cand["status"] == "draft" and cand["graph_diff"][0]["path"] == "config.stt.keywords"
    assert "411001" in cand["config"]["stt"]["keywords"]
    after = (await c.get(f"/api/bots/{bot['id']}")).json()
    assert after["published_version_id"] == published_before  # never published by creation
    assert "411001" not in after["config"]["stt"]["keywords"]

    fault = o["tata-stt"]["fault_profile"]
    before = await _simulate(c, bot, o["tata-stt"], seed=7, fault_profile=fault)
    fixed = await _simulate(c, bot, o["tata-stt"], seed=7, fault_profile=fault,
                            candidate_version_id=cand["candidate_version_id"])
    inline = await _simulate(c, bot, o["tata-stt"], seed=7, fault_profile=fault, patch=patch)
    assert before["passed"] is False and fixed["passed"] is True and inline["passed"] is True
    # no regression on the happy oracle
    assert (await _simulate(c, bot, o["tata-happy"], candidate_version_id=cand["candidate_version_id"]))["passed"]
    # replay with the patch also fixes the recorded failure
    assert (await _replay(c, before["session_id"], "control", patch=patch))["trials"][0]["passed"] is True

    promote = f"/api/bots/{bot['id']}/patch-candidates/{cand['id']}/promote"
    assert (await c.post(promote)).status_code == 403
    pr = await c.post(promote, headers={"X-Approved-By": "asha@reviewer"})
    assert pr.status_code == 200, pr.text
    assert pr.json()["status"] == "promoted" and pr.json()["approved_by"] == "asha@reviewer"
    live = (await c.get(f"/api/bots/{bot['id']}")).json()
    assert live["published_version_id"] == cand["candidate_version_id"]
    assert "411001" in live["config"]["stt"]["keywords"]
    assert (await c.post(promote, headers={"X-Approved-By": "x"})).status_code == 409


async def test_vad_patch_fixes_endpointing_and_graph_patches(tata):
    c, bot, o = tata["client"], tata["bot"], tata["oracles"]
    vad = {"type": "vad_stop_secs", "params": {"delta": 0.4}}
    assert (await _simulate(c, bot, o["tata-endpointing"], patch=vad))["passed"] is True
    for patch, path in (
        ({"type": "confirmation_readback", "params": {"fields": ["pincode"]}}, "nodes.collect_location.fields.pincode.confirm"),
        ({"type": "stricter_validation", "params": {"field": "pincode"}}, "nodes.collect_location.fields.pincode.validation.regex"),
        ({"type": "fallback_route", "params": {"node_id": "lookup_dealers", "target": "transfer_agent"}},
         "nodes.lookup_dealers.transitions.t_lookup_dealers_10.target"),
    ):
        r = await c.post(f"/api/bots/{bot['id']}/patch-candidates", json={"patch": patch})
        assert r.status_code == 201, r.text
        assert path in [d["path"] for d in r.json()["graph_diff"]]
        v = (await c.get(f"/api/bots/{bot['id']}/versions/{r.json()['candidate_version_id']}")).json()
        assert v["is_published"] is False
    bad = await c.post(f"/api/bots/{bot['id']}/patch-candidates", json={"patch": {"type": "stricter_validation", "params": {"field": "nope"}}})
    assert bad.status_code == 422
    assert (await c.post(f"/api/bots/{bot['id']}/patch-candidates", json={"patch": {"type": "rewrite_everything"}})).status_code == 422
    listed = (await c.get(f"/api/bots/{bot['id']}/patch-candidates")).json()
    assert len(listed) == 3
