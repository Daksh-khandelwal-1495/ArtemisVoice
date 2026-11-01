from .factories import node, valid_graph


async def create(client, **kw):
    body = {"name": "Test Bot", **kw}
    r = await client.post("/api/bots", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def add_dealers(db):
    from app.db.models import Dataset, DatasetRow

    ds = Dataset(name="dealers", description="d", columns=["city", "name"])
    db.add(ds)
    await db.flush()
    db.add(DatasetRow(dataset_id=ds.id, data={"city": "Mumbai", "name": "A"}))
    await db.commit()


async def test_health_and_providers(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["db"] is True
    assert set(body["providers"]) == {"deepgram", "openai", "soniox"}

    r = await client.get("/api/providers")
    p = r.json()
    assert set(p) == {"stt", "llm", "tts"}
    for entries in p.values():
        for e in entries:
            assert "env_key" not in e and isinstance(e["configured"], bool)


async def test_bot_crud(client):
    bot = await create(client, description="desc", industry="auto")
    assert bot["slug"] == "test-bot"
    assert bot["draft_version"] == 1 and bot["published_version"] is None
    assert bot["graph"]["initial_node"] == "start" and bot["node_count"] == 2
    assert bot["draft_version_id"]
    assert bot["config"]["stt"]["provider"] == "deepgram"

    # duplicate slug -> 409
    r = await client.post("/api/bots", json={"name": "Other", "slug": "test-bot"})
    assert r.status_code == 409
    # auto-generated slug collision -> suffix
    b2 = await create(client)
    assert b2["slug"] == "test-bot-2"

    # lookup by id and slug
    assert (await client.get(f"/api/bots/{bot['id']}")).json()["slug"] == "test-bot"
    assert (await client.get("/api/bots/test-bot")).json()["id"] == bot["id"]
    assert (await client.get("/api/bots/nope")).status_code == 404

    # list
    lst = (await client.get("/api/bots")).json()
    assert {b["slug"] for b in lst} == {"test-bot", "test-bot-2"}
    s = lst[0]
    for k in ["id", "slug", "name", "description", "industry", "avatar_color", "language", "status", "config",
              "draft_version", "published_version", "node_count", "session_count", "last_session_at", "updated_at"]:
        assert k in s

    # patch
    r = await client.patch(f"/api/bots/{bot['id']}", json={"name": "Renamed", "config": {"tts": {"provider": "openai"}}})
    assert r.status_code == 200
    j = r.json()
    assert j["name"] == "Renamed" and j["config"]["tts"]["provider"] == "openai"
    assert j["config"]["stt"]["provider"] == "deepgram"  # untouched
    r = await client.patch(f"/api/bots/{bot['id']}", json={"config": {"llm": {"temperature": 9}}})
    assert r.status_code == 422
    r = await client.patch(f"/api/bots/{bot['id']}", json={"config": {"tts": {"provider": "nope"}}})
    assert r.status_code == 422
    r = await client.patch(f"/api/bots/{bot['id']}", json={"status": "bogus"})
    assert r.status_code == 422

    # duplicate
    r = await client.post(f"/api/bots/{bot['id']}/duplicate")
    assert r.status_code == 201
    dup = r.json()
    assert dup["slug"] == "test-bot-copy" and dup["graph"] == bot["graph"] and dup["config"]["tts"]["provider"] == "openai"

    # template
    t = await create(client, name="From Template", template="test-bot")
    assert t["config"]["tts"]["provider"] == "openai"
    r = await client.post("/api/bots", json={"name": "X", "template": "missing"})
    assert r.status_code == 400

    # soft delete
    r = await client.delete(f"/api/bots/{b2['id']}")
    assert r.status_code == 204
    assert (await client.get(f"/api/bots/{b2['id']}")).json()["status"] == "archived"
    assert b2["slug"] not in {b["slug"] for b in (await client.get("/api/bots")).json()}
    assert b2["slug"] in {b["slug"] for b in (await client.get("/api/bots?include_archived=true")).json()}
    # hard delete
    r = await client.delete(f"/api/bots/{b2['id']}?hard=true")
    assert r.status_code == 204
    assert (await client.get(f"/api/bots/{b2['id']}")).status_code == 404


async def test_flow_save_versioning_publish(client, db):
    await add_dealers(db)
    bot = await create(client)
    bid = bot["id"]

    r = await client.get(f"/api/bots/{bid}/flow")
    assert r.json()["version"] == 1

    # invalid pydantic graph -> 422, nothing saved
    r = await client.put(f"/api/bots/{bid}/flow", json={"graph": {"initial_node": "x", "nodes": []}})
    assert r.status_code == 422
    r = await client.put(f"/api/bots/{bid}/flow", json={"graph": {"initial_node": "start", "nodes": [
        {"id": "Bad Id", "type": "start", "label": "s"}]}})
    assert r.status_code == 422

    # graph with errors is saved as draft
    bad = valid_graph()
    node(bad, "find_dealer")["transitions"] = []
    r = await client.put(f"/api/bots/{bid}/flow", json={"graph": bad, "note": "broken"})
    assert r.status_code == 200
    j = r.json()
    assert j["version"] == 2 and j["validation"]["valid"] is False
    bad_version_id = j["version_id"]

    # publish latest -> 400 with validation
    r = await client.post(f"/api/bots/{bid}/publish", json={})
    assert r.status_code == 400
    assert r.json()["detail"]["validation"]["valid"] is False

    # validate endpoint
    r = await client.post(f"/api/bots/{bid}/flow/validate", json={"graph": valid_graph()})
    assert r.status_code == 200 and r.json() == {"valid": True, "issues": []}

    # save good graph -> v3, publish
    r = await client.put(f"/api/bots/{bid}/flow", json={"graph": valid_graph(), "note": "good"})
    assert r.json()["version"] == 3 and r.json()["validation"]["valid"] is True
    good_id = r.json()["version_id"]
    r = await client.post(f"/api/bots/{bid}/publish")
    assert r.status_code == 200, r.text
    assert r.json()["published_version"] == 3 and r.json()["status"] == "published"

    # publish a specific older (broken) version -> 400; unknown -> 404
    r = await client.post(f"/api/bots/{bid}/publish", json={"version_id": bad_version_id})
    assert r.status_code == 400
    r = await client.post(f"/api/bots/{bid}/publish", json={"version_id": "00000000-0000-0000-0000-000000000000"})
    assert r.status_code == 404

    # versions list
    vs = (await client.get(f"/api/bots/{bid}/versions")).json()
    assert [v["version"] for v in vs] == [3, 2, 1]
    assert [v["is_published"] for v in vs] == [True, False, False]
    assert vs[0]["node_count"] == 6 and vs[1]["note"] == "broken"

    # restore v1 -> v4 copy of v1
    v1 = vs[2]
    r = await client.post(f"/api/bots/{bid}/versions/{v1['id']}/restore")
    assert r.status_code == 200
    assert r.json()["version"] == 4
    flow = (await client.get(f"/api/bots/{bid}/flow")).json()
    assert flow["version"] == 4 and flow["graph"] == bot["graph"]
    detail = (await client.get(f"/api/bots/{bid}")).json()
    assert detail["draft_version"] == 4 and detail["published_version"] == 3
    assert detail["draft_version_id"] == r.json()["version_id"]
    assert good_id != detail["draft_version_id"]
    r = await client.post(f"/api/bots/{bid}/versions/00000000-0000-0000-0000-000000000000/restore")
    assert r.status_code == 404


async def test_lookup_unknown_dataset_is_error_via_api(client):
    bot = await create(client)
    r = await client.post(f"/api/bots/{bot['id']}/flow/validate", json={"graph": valid_graph()})
    assert any(i["code"] == "lookup_unknown_dataset" for i in r.json()["issues"])
