# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "python-socketio[asyncio_client]>=5.11"]
# ///
"""Load test (PLAN P8 "压测"): N users run agent rounds concurrently against a running stack with oa-mock.

Each virtual user registers, opens a Socket.IO connection, then executes `--rounds` sequential projects
(create chat + first prompt → wait for run:end over the socket). All users run in parallel, so at most
`--users` runs execute concurrently (one sandbox per user — the free plan allows 2).

Reports: run latency p50/p95/p99 (prompt POST → run:end), sandbox+run error rate, events received/sec,
HTTP error count, runs/min, plus server-side histograms scraped from /metrics.

  uv run scripts/loadtest.py --users 20 --rounds 2 --mode lite [--api http://127.0.0.1:8100]

The stack should run with OA_RATE_LIMIT=0 (or a high OA_RATE_AUTH_PER_MIN): all virtual users share one IP.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import time
import uuid
from dataclasses import dataclass, field

import httpx
import socketio

PROMPTS = ["做一个番茄钟页面", "Build a todo list app", "A simple counter with reset", "Landing page for a bakery"]


@dataclass
class Stats:
    latencies: list[float] = field(default_factory=list)
    statuses: dict[str, int] = field(default_factory=dict)
    http_errors: list[str] = field(default_factory=list)
    events: int = 0
    timeouts: int = 0


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


async def vuser(i: int, a: argparse.Namespace, stats: Stats, start: asyncio.Event, ready: asyncio.Queue) -> None:
    async with httpx.AsyncClient(base_url=a.api + "/api/v1", timeout=120, trust_env=False) as c:
        r = await c.post("/user/register", json={"email": f"lt-{uuid.uuid4().hex[:10]}@example.com",
                                                 "password": "loadtest123", "name": f"LT{i}"})
        body = r.json()
        if body.get("code") != 0:
            stats.http_errors.append(f"register {r.status_code} {body.get('message')}")
            return
        tok = body["data"]["access_token"]
        c.headers["Authorization"] = tok
        ends: dict[str, asyncio.Future] = {}
        sio = socketio.AsyncClient(reconnection=True)

        @sio.on("*")
        async def _any(event, data):  # noqa: ANN001
            stats.events += 1
            if event == "run:end" and isinstance(data, dict):
                f = ends.get(data.get("chat_id"))
                if f is not None and not f.done():  # suspended (e.g. build-fix card) is a terminal outcome here
                    f.set_result(data.get("status"))

        joined: dict[str, asyncio.Future] = {}

        @sio.on("join_chat_ack")
        async def _ack(data):  # noqa: ANN001
            f = joined.get(data.get("chat_id"))
            if f is not None and not f.done():
                f.set_result(data.get("ok"))

        await sio.connect(a.api, transports=["websocket"], socketio_path="/socket.io", auth={"token": tok})
        ready.put_nowait(i)
        await start.wait()
        try:
            for rnd in range(a.rounds):
                prompt = PROMPTS[(i + rnd) % len(PROMPTS)]
                r = await c.post("/chats", json={"title": f"lt {i}.{rnd}", "config": {"agent_mode": a.mode}})
                b = r.json()
                if b.get("code") != 0:
                    stats.http_errors.append(f"create {r.status_code} {b.get('message')}")
                    continue
                cid = b["data"]["chat_id"]
                loop = asyncio.get_running_loop()
                ends[cid] = loop.create_future()
                joined[cid] = loop.create_future()
                await sio.emit("join_chat", {"chat_id": cid})
                await asyncio.wait_for(joined[cid], 10)
                t0 = time.perf_counter()
                r = await c.post(f"/chats/{cid}/messages", json={"content": [{"insert": prompt}],
                                                                 "metadata": {"agent_mode": a.mode}})
                b = r.json()
                if b.get("code") != 0:
                    stats.http_errors.append(f"send {r.status_code} {b.get('message')}")
                    continue
                try:
                    status = await asyncio.wait_for(ends[cid], a.timeout)
                    stats.latencies.append(time.perf_counter() - t0)
                    stats.statuses[status] = stats.statuses.get(status, 0) + 1
                except TimeoutError:
                    stats.timeouts += 1
                if a.stop_sandboxes:  # free the per-user sandbox slot like an idle reaper would
                    await c.post(f"/chats/{cid}/runtime/stop")
        finally:
            await sio.disconnect()


def scrape(api: str) -> dict[str, float]:
    try:
        text = httpx.get(api + "/metrics", timeout=10, trust_env=False).text
    except Exception:
        return {}
    out: dict[str, float] = {}
    for line in text.splitlines():
        m = re.match(r"^(oa_[a-z_]+(?:_sum|_count))\{?([^}]*)\}? ([0-9.e+-]+)$", line)
        if m:
            out[f"{m.group(1)}{{{m.group(2)}}}"] = float(m.group(3))
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8100")
    ap.add_argument("--users", type=int, default=10)
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--mode", default="lite", choices=["lite", "full"])
    ap.add_argument("--timeout", type=float, default=900)
    ap.add_argument("--stop-sandboxes", action="store_true", default=True)
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    before = scrape(a.api)
    stats = Stats()
    start = asyncio.Event()
    ready: asyncio.Queue = asyncio.Queue()
    tasks = [asyncio.create_task(vuser(i, a, stats, start, ready)) for i in range(a.users)]
    # synchronized start once every user registered + connected (or failed doing so)
    deadline = time.perf_counter() + 120
    n_ready = 0
    while n_ready + sum(t.done() for t in tasks) < a.users and time.perf_counter() < deadline:
        try:
            await asyncio.wait_for(ready.get(), 1)
            n_ready += 1
        except TimeoutError:
            pass
    t0 = time.perf_counter()
    start.set()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    wall = time.perf_counter() - t0
    crashes = [repr(r) for r in results if isinstance(r, BaseException)]
    after = scrape(a.api)

    total = a.users * a.rounds
    ok = stats.statuses.get("success", 0)
    failed = total - ok
    report = {
        "users": a.users, "rounds": a.rounds, "mode": a.mode, "runs_total": total, "runs_ok": ok,
        "status_counts": stats.statuses, "timeouts": stats.timeouts, "http_errors": len(stats.http_errors),
        "client_crashes": len(crashes), "error_rate": round(failed / total, 4) if total else 0,
        "wall_s": round(wall, 1), "runs_per_min": round(ok / wall * 60, 1) if wall else 0,
        "latency_s": {"p50": round(pct(stats.latencies, .5), 2), "p95": round(pct(stats.latencies, .95), 2),
                      "p99": round(pct(stats.latencies, .99), 2), "max": round(max(stats.latencies, default=0), 2),
                      "mean": round(statistics.fmean(stats.latencies), 2) if stats.latencies else None},
        "events_received": stats.events, "events_per_s": round(stats.events / wall, 1) if wall else 0,
        "server": {k: round(after.get(k, 0) - before.get(k, 0), 3) for k in after
                   if k.startswith(("oa_sandbox_start_seconds", "oa_run_duration_seconds"))
                   and after.get(k, 0) != before.get(k, 0)},
        "sample_errors": (stats.http_errors + crashes)[:5],
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    asyncio.run(main())
