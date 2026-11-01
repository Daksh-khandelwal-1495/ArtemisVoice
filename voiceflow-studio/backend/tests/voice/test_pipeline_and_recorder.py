"""Pipeline smoke test (no network), recorder persistence and router error paths."""

import json
import uuid
from pathlib import Path

from sqlalchemy import select

from app.db.models import Bot, CallSession, CapturedField, Dataset, DatasetRow, FlowVersion, SessionEvent
from app.db.session import SessionLocal
from app.schemas.flow import FlowGraph
from app.schemas.providers import BotConfig
from app.voice.providers import ProviderKeys
from app.voice.recorder import SessionRecorder, mask_payload

SEED = Path(__file__).resolve().parents[2] / "app" / "seed" / "bots" / "tata-motors.json"
KEYS = ProviderKeys(deepgram="dg", soniox="sx", openai="oa")


def _seed():
    return json.loads(SEED.read_text())


async def test_build_full_pipeline_with_dummy_keys():
    from pipecat.flows import FlowManager
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection

    from app.voice.bot import CallContext, build_call, build_transport

    data = _seed()
    ctx = CallContext(bot_id=uuid.uuid4(), bot_name=data["name"], mode="draft", flow_version_id=None, flow_version=1,
                      graph=FlowGraph.model_validate(data["graph"]), config=BotConfig.model_validate(data.get("config") or {}))
    call = build_call(ctx, build_transport(SmallWebRTCConnection()), keys=KEYS)
    assert isinstance(call.worker, PipelineWorker)
    assert isinstance(call.flow_manager, FlowManager)
    names = [type(p).__name__ for p in call.pipeline.processors]
    i = names.index(type(call.stt).__name__)
    assert names[i - 1] == "SmallWebRTCInputTransport"
    assert names[i:i + 6] == [type(call.stt).__name__, "LLMUserAggregator", "OpenAILLMService", type(call.tts).__name__,
                          "SmallWebRTCOutputTransport", "LLMAssistantAggregator"]
    assert call.runtime.vars["bot_name"] == data["name"]
    first = await call.runtime.initial()
    assert isinstance(first, dict) and first["name"] == data["graph"]["initial_node"]


def test_mask_payload():
    p = {"field": "pan", "value": "ABCDE1234F", "args": {"pan": "ABCDE1234F", "name": "Ravi"}}
    out = mask_payload(p, {"pan"})
    assert out["value"] == "******234F" and out["args"]["pan"] == "******234F" and out["args"]["name"] == "Ravi"


async def _make_bot(db, graph=None, published=False, config=None):
    bot = Bot(slug=f"b-{uuid.uuid4().hex[:6]}", name="Test", config=config or {})
    db.add(bot)
    await db.flush()
    fv = FlowVersion(bot_id=bot.id, version=1, graph=graph or _seed()["graph"])
    db.add(fv)
    await db.flush()
    if published:
        bot.published_version_id = fv.id
    await db.commit()
    return bot, fv


async def test_recorder_persists_and_survives_errors(db):
    bot, fv = await _make_bot(db)
    rec = SessionRecorder(bot_id=bot.id, flow_version_id=fv.id, mode="draft", config_snapshot={"a": 1},
                          sensitive_fields={"phone"}, session_factory=SessionLocal)
    await rec.start()
    rec.event("node_enter", "start", {"label": "Start"})
    rec.event("field_captured", "c", {"field": "phone", "value": "9876543210"})
    rec.field("c", "phone", "9876543210", False, 1)
    rec.field("c", "phone", "9876543210", True, 2)
    rec.field("c", "city", "Pune", True, 1)
    rec.update(current_node="c", variables={"phone": "9876543210", "city": "Pune"})
    rec.event("bad", "c", {"x": object()})  # non-JSON payload -> stringified
    await rec.finish("completed", "booked", {"city": "Pune", "phone": "9876543210"})

    async with SessionLocal() as s:
        cs = await s.get(CallSession, rec.session_id)
        assert cs.status == "completed" and cs.end_reason == "booked" and cs.duration_secs is not None
        assert cs.current_node == "c" and cs.variables["city"] == "Pune" and cs.ended_at is not None
        evs = (await s.execute(select(SessionEvent).where(SessionEvent.session_id == rec.session_id))).scalars().all()
        cap = [e for e in evs if e.type == "field_captured"][0]
        assert cap.payload["value"] == "******3210"
        fields = {f.field_name: f for f in (await s.execute(select(CapturedField).where(CapturedField.session_id == rec.session_id))).scalars()}
        assert fields["phone"].valid is True and fields["phone"].attempts == 2 and fields["phone"].value == "9876543210"
        assert len(fields) == 2

    class Broken:
        def __call__(self):
            raise RuntimeError("db down")

    bad = SessionRecorder(bot_id=bot.id, flow_version_id=None, mode="draft", session_factory=Broken())
    await bad.start()
    bad.event("node_enter", "x", {})
    await bad.finish("error", "x")
    assert bad.errors >= 2  # logged, not raised


async def test_lookup_dataset_rows(db):
    from app.voice.bot import lookup_dataset_rows

    ds = Dataset(name=f"d_{uuid.uuid4().hex[:6]}", columns=["city", "name"])
    db.add(ds)
    await db.flush()
    db.add_all([DatasetRow(dataset_id=ds.id, data={"city": "Pune", "name": "A"}),
                DatasetRow(dataset_id=ds.id, data={"city": "Navi Mumbai", "name": "B"})])
    await db.commit()
    assert [r["name"] for r in await lookup_dataset_rows(ds.name, {"city": "pune"}, "exact", 5)] == ["A"]
    assert [r["name"] for r in await lookup_dataset_rows(ds.name, {"city": "mumbai"}, "icontains", 5)] == ["B"]
    assert await lookup_dataset_rows(ds.name, {"city": None}, "exact", 5) == []


async def test_offer_errors(client, db, monkeypatch):
    r = await client.post(f"/api/bots/{uuid.uuid4()}/offer", json={"sdp": "x", "type": "offer"})
    assert r.status_code == 404
    bot, _ = await _make_bot(db)
    r = await client.post(f"/api/bots/{bot.id}/offer?mode=published", json={"sdp": "x", "type": "offer"})
    assert r.status_code == 409
    from app.core.config import settings

    monkeypatch.setattr(settings, "deepgram_api_key", "")
    r = await client.post(f"/api/bots/{bot.slug}/offer", json={"sdp": "x", "type": "offer"})
    assert r.status_code == 503
    assert "DEEPGRAM_API_KEY" in r.json()["detail"]["message"]


async def test_preview_prompt(client, db):
    bot, _ = await _make_bot(db)
    graph = _seed()["graph"]
    r = await client.get(f"/api/bots/{bot.id}/preview-prompt")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["node_id"] == graph["initial_node"]
    assert {"request_human_agent", "end_call"} <= {t["name"] for t in body["tools"]}
    collect = next(n["id"] for n in graph["nodes"] if n["type"] == "collect")
    r = await client.get(f"/api/bots/{bot.id}/preview-prompt", params={"node_id": collect})
    assert any(t["name"] == f"submit_{collect}" for t in r.json()["tools"])
    assert (await client.get(f"/api/bots/{bot.id}/preview-prompt", params={"node_id": "nope"})).status_code == 404
