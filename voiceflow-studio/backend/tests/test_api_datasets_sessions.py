from datetime import datetime, timedelta, timezone

from app.db.models import CallSession, CapturedField, Dataset, DatasetRow, FlowVersion, SessionEvent

from .factories import valid_graph


async def test_datasets(client, db):
    ds = Dataset(name="dealers", description="Dealers", columns=["city", "name"])
    db.add(ds)
    await db.flush()
    db.add_all([DatasetRow(dataset_id=ds.id, data={"city": c, "name": f"Dealer {i}"})
                for i, c in enumerate(["Mumbai", "Pune", "Mumbai", "Delhi"])])
    await db.commit()

    lst = (await client.get("/api/datasets")).json()
    assert lst == [{"id": str(ds.id), "name": "dealers", "description": "Dealers",
                    "columns": ["city", "name"], "row_count": 4}]

    r = (await client.get("/api/datasets/dealers/rows")).json()
    assert r["total"] == 4 and len(r["items"]) == 4
    r = (await client.get("/api/datasets/dealers/rows?q=mumbai")).json()
    assert r["total"] == 2
    r = (await client.get("/api/datasets/dealers/rows?limit=1&offset=1")).json()
    assert r["total"] == 4 and len(r["items"]) == 1
    assert (await client.get("/api/datasets/nope/rows")).status_code == 404

    r = await client.post("/api/datasets/dealers/rows", json={"data": {"city": "Goa", "name": "N", "pin": "403001"}})
    assert r.status_code == 201
    row_id = r.json()["id"]
    lst = (await client.get("/api/datasets")).json()
    assert lst[0]["row_count"] == 5 and lst[0]["columns"] == ["city", "name", "pin"]

    assert (await client.delete(f"/api/datasets/dealers/rows/{row_id}")).status_code == 204
    assert (await client.delete(f"/api/datasets/dealers/rows/{row_id}")).status_code == 404
    assert (await client.get("/api/datasets")).json()[0]["row_count"] == 4


async def _make_sessions(client, db):
    r = await client.post("/api/bots", json={"name": "Sess Bot"})
    bot = r.json()
    r = await client.put(f"/api/bots/{bot['id']}/flow", json={"graph": valid_graph()})
    vid = r.json()["version_id"]
    fv = await db.get(FlowVersion, __import__("uuid").UUID(vid))
    now = datetime.now(timezone.utc)
    s1 = CallSession(bot_id=fv.bot_id, flow_version_id=fv.id, mode="draft", status="completed",
                     end_reason="completed", current_node="done", variables={"name": "Asha", "phone": "9876543210"},
                     started_at=now - timedelta(minutes=5), ended_at=now, duration_secs=120)
    s2 = CallSession(bot_id=fv.bot_id, flow_version_id=fv.id, mode="draft", status="abandoned",
                     current_node="collect_info", started_at=now - timedelta(minutes=1), duration_secs=30)
    db.add_all([s1, s2])
    await db.flush()
    db.add_all([
        SessionEvent(session_id=s1.id, type="node_enter", node_id="collect_info", payload={}),
        SessionEvent(session_id=s1.id, type="field_captured", node_id="collect_info",
                     payload={"field": "phone", "value": "9876543210"}),
        SessionEvent(session_id=s2.id, type="node_enter", node_id="collect_info", payload={}),
        CapturedField(session_id=s1.id, node_id="collect_info", field_name="name", value="Asha", valid=True),
        CapturedField(session_id=s1.id, node_id="collect_info", field_name="phone", value="9876543210", valid=True),
        CapturedField(session_id=s1.id, node_id="collect_info", field_name="city", value="Mumbai", valid=True),
        CapturedField(session_id=s2.id, node_id="collect_info", field_name="name", value="Ravi", valid=True),
    ])
    await db.commit()
    return bot, s1, s2


async def test_sessions_listing_and_detail(client, db):
    bot, s1, s2 = await _make_sessions(client, db)

    r = (await client.get("/api/sessions")).json()
    assert r["total"] == 2
    assert [i["id"] for i in r["items"]] == [str(s2.id), str(s1.id)]  # newest first
    first = r["items"][0]
    for k in ["id", "bot_id", "bot_name", "status", "end_reason", "mode", "started_at", "ended_at",
              "duration_secs", "captured_count", "required_missing"]:
        assert k in first
    assert first["bot_name"] == "Sess Bot" and first["captured_count"] == 1
    assert sorted(first["required_missing"]) == ["city", "phone"]
    assert r["items"][1]["required_missing"] == []

    r = (await client.get("/api/sessions?status=completed")).json()
    assert r["total"] == 1 and r["items"][0]["id"] == str(s1.id)
    r = (await client.get(f"/api/sessions?bot_id={bot['slug']}&limit=1")).json()
    assert r["total"] == 2 and len(r["items"]) == 1
    r = (await client.get("/api/sessions?limit=1&offset=1")).json()
    assert r["items"][0]["id"] == str(s1.id)

    d = (await client.get(f"/api/sessions/{s1.id}")).json()
    cap = {c["field_name"]: c for c in d["captured"]}
    assert cap["phone"]["value"] == "******3210" and cap["phone"]["sensitive"] is True
    assert cap["name"]["value"] == "Asha"
    assert d["variables"]["phone"] == "******3210"
    ev = next(e for e in d["events"] if e["type"] == "field_captured")
    assert ev["payload"]["value"] == "******3210"
    assert len(d["events"]) == 2

    d = (await client.get(f"/api/sessions/{s1.id}?reveal=true")).json()
    assert {c["field_name"]: c["value"] for c in d["captured"]}["phone"] == "9876543210"
    assert d["variables"]["phone"] == "9876543210"

    assert (await client.get("/api/sessions/00000000-0000-0000-0000-000000000000")).status_code == 404
    assert (await client.get("/api/sessions/garbage")).status_code == 404


async def test_bot_stats(client, db):
    bot, s1, s2 = await _make_sessions(client, db)
    st = (await client.get(f"/api/bots/{bot['id']}/stats")).json()
    assert st["total_sessions"] == 2 and st["completed"] == 1 and st["abandoned"] == 1
    assert st["transferred"] == 0
    assert st["avg_duration_secs"] == 75.0
    assert st["completion_rate"] == 0.5
    assert st["field_capture_rates"] == {"name": 1.0, "phone": 0.5, "city": 0.5}
    summary = (await client.get(f"/api/bots/{bot['slug']}")).json()
    assert summary["session_count"] == 2 and summary["last_session_at"]
