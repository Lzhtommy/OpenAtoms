from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import update

from oa.db import models as m
from oa.db.session import SessionLocal
from tests.conftest import register_user


def D(text: str) -> list[dict]:
    return [{"insert": text}]


async def _set_running(chat_id: str, running: bool) -> None:
    async with SessionLocal() as s:
        await s.execute(update(m.Chat).where(m.Chat.id == chat_id).values(is_running=running))
        await s.commit()


async def test_chat_crud_and_first_message(client, fake_runtime):
    runner, _ = fake_runtime
    u = await register_user(client)
    h = u["headers"]
    r = await client.post("/api/v1/chats", headers=h, json={
        "first_message": {"content": D("Build a pomodoro timer app with stats"), "metadata": {"agent_mode": "lite"}},
        "config": {"agent_mode": "lite"}})
    chat = r.json()["data"]
    cid = chat["chat_id"]
    assert len(cid) == 32 and chat["title"] == "Build a pomodoro timer app wit"
    assert chat["config"]["agent_mode"] == "lite"
    assert chat["uris"]["dev"].startswith("http://5173-" + cid + "-preview.")
    assert runner.started and runner.started[0][0] == cid

    r = await client.get("/api/v1/chats", headers=h)
    page = r.json()["data"]
    assert page["total"] == 1 and page["items"][0]["chat_id"] == cid

    r = await client.patch(f"/api/v1/chats/{cid}", headers=h, json={"title": "Pomodoro", "is_starred": True,
                                                                     "access_mode": "public"})
    assert r.json()["data"]["title"] == "Pomodoro" and r.json()["data"]["is_starred"]
    assert (await client.get("/api/v1/chats?is_starred=true", headers=h)).json()["data"]["total"] == 1
    assert (await client.get("/api/v1/chats?keyword=pomo", headers=h)).json()["data"]["total"] == 1

    # stranger can read public chat but not write
    other = await register_user(client)
    assert (await client.get(f"/api/v1/chats/{cid}", headers=other["headers"])).status_code == 200
    r = await client.patch(f"/api/v1/chats/{cid}", headers=other["headers"], json={"title": "x"})
    assert r.status_code == 403 and r.json()["code"] == 4403
    await client.patch(f"/api/v1/chats/{cid}", headers=h, json={"access_mode": "private"})
    assert (await client.get(f"/api/v1/chats/{cid}", headers=other["headers"])).status_code == 403

    # folders
    f = (await client.post("/api/v1/folders", headers=h, json={"name": "Work"})).json()["data"]
    await client.patch(f"/api/v1/chats/{cid}", headers=h, json={"folder_id": f["id"]})
    assert (await client.get(f"/api/v1/chats?folder_id={f['id']}", headers=h)).json()["data"]["total"] == 1

    r = await client.delete(f"/api/v1/chats/{cid}", headers=h)
    assert r.json()["code"] == 0
    assert (await client.get(f"/api/v1/chats/{cid}", headers=h)).status_code == 404


async def test_configs_and_models(client):
    u = await register_user(client)
    data = (await client.get("/api/v1/configs/general", headers=u["headers"])).json()["data"]
    assert data["default_model"] == "oa-mock" and any(x["id"] == "oa-mock" for x in data["models"])


async def test_workspace_invite_and_rbac(client, fake_runtime):
    owner = await register_user(client)
    wid = owner["user"]["current_workspace_id"]
    cid = (await client.post("/api/v1/chats", headers=owner["headers"], json={"title": "Team app"})).json()["data"]["chat_id"]
    inv = (await client.post(f"/api/v1/workspaces/{wid}/invitations", headers=owner["headers"],
                             json={"email": "mate@example.com", "role": "viewer"})).json()["data"]
    assert inv["url"].endswith(inv["token"])
    mate = await register_user(client, email="mate@example.com")
    ws = (await client.post(f"/api/v1/workspaces/invitations/{inv['token']}/accept", headers=mate["headers"])).json()["data"]
    assert ws["id"] == wid and ws["role"] == "viewer"
    members = (await client.get(f"/api/v1/workspaces/{wid}/members", headers=mate["headers"])).json()["data"]
    assert len(members) == 2

    res = (await client.post("/api/v1/rbac/batch-check", headers=mate["headers"], json={"checks": [
        {"resource": "chat", "id": cid, "action": "read"}, {"resource": "chat", "id": cid, "action": "write"}]})).json()
    assert res["data"]["results"] == [True, False]

    await client.patch(f"/api/v1/workspaces/{wid}/members/{mate['user']['id']}", headers=owner["headers"], json={"role": "member"})
    res = (await client.post("/api/v1/rbac/batch-check", headers=mate["headers"], json={"checks": [
        {"resource": "chat", "id": cid, "action": "write"}]})).json()
    assert res["data"]["results"] == [True]
    # workspace header selects workspace for listings
    lst = (await client.get("/api/v1/chats", headers={**mate["headers"], "X-Workspace-Id": str(wid)})).json()["data"]
    assert lst["total"] == 1

    r = await client.delete(f"/api/v1/workspaces/{wid}/members/{mate['user']['id']}", headers=owner["headers"])
    assert r.json()["code"] == 0
    assert (await client.get(f"/api/v1/chats/{cid}", headers=mate["headers"])).status_code == 403


async def test_messages_queue_branches_and_cards(client, fake_runtime):
    runner, _ = fake_runtime
    u = await register_user(client)
    h = u["headers"]
    cid = (await client.post("/api/v1/chats", headers=h, json={"title": "Q"})).json()["data"]["chat_id"]

    m1 = (await client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": D("first")})).json()["data"]
    assert m1["queued"] is False and m1["message"]["round_id"] == 1
    # agent reply in the same round
    async with SessionLocal() as s:
        s.add(m.Message(chat_id=cid, role="Engineer", name="Alex", type="message", content=D("done 1"), round_id=1, attempt=0))
        await s.commit()

    # while running → queued
    await _set_running(cid, True)
    q = (await client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": D("queued one")})).json()["data"]
    assert q["queued"] is True and q["message"] is None
    q2 = (await client.post(f"/api/v1/chats/{cid}/prompt-queue", headers=h, json={"content": D("queued two")})).json()["data"]
    snap = (await client.get(f"/api/v1/chats/{cid}/prompt-queue", headers=h)).json()["data"]
    assert [i["content"][0]["insert"] for i in snap["items"]] == ["queued one", "queued two"]
    await client.post(f"/api/v1/chats/{cid}/prompt-queue/reorder", headers=h, json={"ids": [q2["id"]]})
    await client.patch(f"/api/v1/chats/{cid}/prompt-queue/{q2['id']}", headers=h, json={"content": D("queued 2!")})
    snap = (await client.get(f"/api/v1/chats/{cid}/prompt-queue", headers=h)).json()["data"]
    assert snap["items"][0]["content"][0]["insert"] == "queued 2!"

    # run ends → pop_next sends first queued item as a new round
    await _set_running(cid, False)
    from oa.domain.queue import pop_next

    started = len(runner.started)
    await pop_next(cid)
    assert len(runner.started) == started + 1
    hist = (await client.get(f"/api/v1/chats/{cid}/messages", headers=h)).json()["data"]
    users = [x for x in hist["messages"] if x["role"] == "User"]
    assert [x["content"][0]["insert"] for x in users] == ["first", "queued 2!"]
    assert users[1]["round_id"] == 2 and users[1]["prev_round_id"] == 1
    snap = (await client.get(f"/api/v1/chats/{cid}/prompt-queue", headers=h)).json()["data"]
    assert len(snap["items"]) == 1

    # pause blocks pop
    await client.post(f"/api/v1/chats/{cid}/prompt-queue/pause", headers=h, json={"paused": True})
    await pop_next(cid)
    assert len((await client.get(f"/api/v1/chats/{cid}/prompt-queue", headers=h)).json()["data"]["items"]) == 1
    await client.delete(f"/api/v1/chats/{cid}/prompt-queue/{snap['items'][0]['id']}", headers=h)

    # edit round 1 → attempt 1; later round 2 hidden on the new branch
    first_id = users[0]["id"]
    e = (await client.post(f"/api/v1/chats/{cid}/messages/{first_id}/edit", headers=h, json={"content": D("first v2")})).json()["data"]
    assert e["message"]["attempt"] == 1 and e["message"]["round_id"] == 1
    hist = (await client.get(f"/api/v1/chats/{cid}/messages", headers=h)).json()["data"]
    texts = [x["content"][0]["insert"] for x in hist["messages"] if x["role"] == "User"]
    assert texts == ["first v2"]
    assert hist["messages"][0]["attempts"] == 2
    # switch back to attempt 0 → old branch incl. round 2 returns
    await client.put(f"/api/v1/chats/{cid}/round-state", headers=h, json={"active_attempts": {"1": 0}})
    hist = (await client.get(f"/api/v1/chats/{cid}/messages", headers=h)).json()["data"]
    texts = [x["content"][0]["insert"] for x in hist["messages"] if x["role"] in ("User", "Engineer")]
    assert texts == ["first", "done 1", "queued 2!"]

    # retry creates attempt 2 with same content
    r = (await client.post(f"/api/v1/chats/{cid}/messages/{first_id}/retry", headers=h)).json()["data"]
    assert r["message"]["attempt"] == 2 and r["message"]["content"] == D("first")

    # ask_human: task card click resumes the suspended run in the same round
    async with SessionLocal() as s:
        card = m.Message(chat_id=cid, role="Engineer", name="Alex", type="task", round_id=1, attempt=2,
                         content={"name": "Continue?", "description": "", "status": "pending",
                                  "option": [{"label": "Yes", "content": "Yes"}, {"label": "No", "content": "No", "danger": True}]})
        s.add(card)
        await s.commit()
        card_id = card.id
    runner.suspended[cid] = SimpleNamespace(round_id=1, attempt=2, suspend_reason="ask_human", suspend_message_id=card_id)
    rep = (await client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={
        "content": D("Yes"), "metadata": {"task_reply": {"message_id": card_id, "option_index": 0}}})).json()["data"]
    assert rep["message"]["round_id"] == 1 and rep["message"]["attempt"] == 2
    assert runner.resumed[-1][1] == "reply"
    async with SessionLocal() as s:
        c = await s.get(m.Message, card_id)
        assert c.content["status"] == "finished" and c.content["selected"] == 0

    # plan approval
    async with SessionLocal() as s:
        plan = m.Message(chat_id=cid, role="Team Leader", name="Mike", type="plan_ready", round_id=1, attempt=2,
                         content={"summary": "plan", "tasks": [], "status": "pending"})
        s.add(plan)
        await s.commit()
        plan_id = plan.id
    r = await client.post(f"/api/v1/chats/{cid}/approve-plan", headers=h)
    assert r.json()["code"] == 4409
    runner.suspended[cid] = SimpleNamespace(round_id=1, attempt=2, suspend_reason="plan_ready", suspend_message_id=plan_id)
    assert (await client.post(f"/api/v1/chats/{cid}/approve-plan", headers=h)).json()["code"] == 0
    assert runner.resumed[-1][1] == "approve"
    async with SessionLocal() as s:
        assert (await s.get(m.Message, plan_id)).content["status"] == "approved"

    # search / stop / events replay
    found = (await client.get(f"/api/v1/chats/{cid}/messages/search?keyword=queued", headers=h)).json()["data"]["items"]
    assert found
    assert (await client.post(f"/api/v1/chats/{cid}/stop", headers=h)).json()["data"]["cancelled"] is True
    ev = (await client.get(f"/api/v1/chats/{cid}/events?after=0", headers=h)).json()["data"]
    assert ev["complete"] and ev["events"][0]["seq"] == 1
    names = {e["event"] for e in ev["events"]}
    assert {"msg:create", "prompt_queue:update", "msg:update"} <= names
    last = ev["events"][-1]["seq"]
    ev2 = (await client.get(f"/api/v1/chats/{cid}/events?after={last - 1}", headers=h)).json()["data"]
    assert len(ev2["events"]) == 1 and ev2["complete"]


async def test_quota_exhausted(client, fake_runtime):
    runner, _ = fake_runtime
    u = await register_user(client)
    h = u["headers"]
    async with SessionLocal() as s:
        await s.execute(update(m.Workspace).where(m.Workspace.id == u["user"]["current_workspace_id"]).values(credits=0))
        await s.commit()
    cid = (await client.post("/api/v1/chats", headers=h, json={"title": "Q"})).json()["data"]["chat_id"]
    before = len(runner.started)
    await client.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": D("hi")})
    assert len(runner.started) == before
    hist = (await client.get(f"/api/v1/chats/{cid}/messages", headers=h)).json()["data"]
    assert hist["messages"][-1]["type"] == "quota"
