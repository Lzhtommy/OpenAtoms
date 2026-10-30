"""Socket.IO against a real uvicorn server: auth on connect, join_chat ack, event fan-out with seq."""
from __future__ import annotations

import asyncio
import socket

import pytest
import socketio
import uvicorn

from oa.core.events import bus
from tests.conftest import register_user


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def live_server(client):
    from oa.main import app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    await task


async def test_socket_auth_join_and_events(client, live_server, fake_runtime):
    u = await register_user(client)
    other = await register_user(client)
    cid = (await client.post("/api/v1/chats", headers=u["headers"], json={"title": "rt"})).json()["data"]["chat_id"]

    bad = socketio.AsyncClient(reconnection=False)
    with pytest.raises(socketio.exceptions.ConnectionError):
        await bad.connect(live_server, transports=["websocket"], auth={"token": "nope"}, wait_timeout=5)

    received: list[tuple[str, dict]] = []
    acks: list[dict] = []
    sio = socketio.AsyncClient(reconnection=False)
    sio.on("join_chat_ack", lambda d: acks.append(d))
    sio.on("msg:create", lambda d: received.append(("msg:create", d)))
    sio.on("chat:update", lambda d: received.append(("chat:update", d)))
    await sio.connect(f"{live_server}?token={u['token']}", transports=["websocket"], wait_timeout=5)
    ack = await sio.call("join_chat", {"chat_id": cid}, timeout=5)
    assert ack == {"chat_id": cid, "ok": True, "code": 0}

    # private chat: other user is refused
    sio2 = socketio.AsyncClient(reconnection=False)
    await sio2.connect(live_server, transports=["websocket"], auth={"token": other["token"]}, wait_timeout=5)
    ack2 = await sio2.call("join_chat", {"chat_id": cid}, timeout=5)
    assert ack2["ok"] is False and ack2["code"] == 4403
    assert (await sio2.call("join_chat", {"chat_id": "0" * 32}, timeout=5))["code"] == 4404

    await client.post(f"/api/v1/chats/{cid}/messages", headers=u["headers"], json={"content": [{"insert": "hello"}]})
    await bus.emit(cid, "chat:update", {"chat": {"title": "x"}})
    for _ in range(100):
        if len(received) >= 2:
            break
        await asyncio.sleep(0.05)
    kinds = [k for k, _ in received]
    assert "msg:create" in kinds and "chat:update" in kinds
    seqs = [d["seq"] for _, d in received]
    assert seqs == sorted(seqs) and all(d["chat_id"] == cid for _, d in received)
    assert acks and acks[0]["ok"]
    await sio.disconnect()
    await sio2.disconnect()
