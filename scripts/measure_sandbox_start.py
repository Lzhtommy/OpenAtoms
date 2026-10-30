# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""Measure sandbox wake latency (PLAN P8 cold start vs warm-pool claim) against a running stack.

Scenario "re-open an existing project": each project is started once (template copy + pnpm install) and stopped;
then each is woken again and timed from POST /runtime/start until chat status == running (dev server launched).
Run once with OA_SANDBOX_WARM_POOL=0 (cold) and once with a pool ≥ --projects (warm), compare.

  uv run scripts/measure_sandbox_start.py --api http://127.0.0.1:8400 --projects 3
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import time
import uuid

import httpx


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8100")
    ap.add_argument("--projects", type=int, default=3)
    ap.add_argument("--settle", type=float, default=20, help="seconds to let the warm pool refill before waking")
    a = ap.parse_args()
    c = httpx.Client(base_url=a.api + "/api/v1", timeout=120, trust_env=False)

    def call(method: str, path: str, **kw):  # noqa: ANN003, ANN202
        b = c.request(method, path, **kw).json()
        if b["code"] != 0:
            raise RuntimeError(f"{path}: {b}")
        return b["data"]

    tok = call("POST", "/user/register", json={"email": f"ms-{uuid.uuid4().hex[:8]}@example.com", "password": "pass12345"})
    c.headers["Authorization"] = tok["access_token"]
    # free plan allows 2 concurrent sandboxes; projects are woken one at a time
    chats = [call("POST", "/chats", json={"title": f"m{i}"})["chat_id"] for i in range(a.projects)]

    def wait_status(cid: str, want: str, timeout: float = 300) -> float:
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            st = call("GET", f"/chats/{cid}")["metadata"]["status"]
            if st == want:
                return time.perf_counter() - t0
            if st == "failed":
                raise RuntimeError(f"sandbox {cid} failed")
            time.sleep(0.05)
        raise RuntimeError(f"timeout waiting for {want}")

    first = []
    for cid in chats:  # first open: copy template + install deps
        t0 = time.perf_counter()
        call("POST", f"/chats/{cid}/runtime/start")
        wait_status(cid, "running")
        first.append(time.perf_counter() - t0)
        call("POST", f"/chats/{cid}/runtime/stop")
        wait_status(cid, "stopped")
    time.sleep(a.settle)
    metrics_before = httpx.get(a.api + "/metrics", trust_env=False).text
    wake = []
    for cid in chats:
        t0 = time.perf_counter()
        call("POST", f"/chats/{cid}/runtime/start")
        wait_status(cid, "running")
        wake.append(time.perf_counter() - t0)
        call("POST", f"/chats/{cid}/runtime/stop")
        wait_status(cid, "stopped")
        time.sleep(1)
    metrics_after = httpx.get(a.api + "/metrics", trust_env=False).text

    def kinds(text: str, stat: str = "count") -> dict[str, float]:
        out = {}
        pat = r'oa_sandbox_start_seconds_' + stat + r'\{kind="(\w+)",provider="(\w+)"\} ([0-9.e+-]+)'
        for m in re.finditer(pat, text):
            out[f"{m.group(2)}/{m.group(1)}"] = float(m.group(3))
        return out

    kb, ka = kinds(metrics_before), kinds(metrics_after)
    sb, sa = kinds(metrics_before, "sum"), kinds(metrics_after, "sum")
    print(json.dumps({
        "first_open_s": [round(x, 2) for x in first],
        "wake_s": [round(x, 2) for x in wake],
        "wake_median_s": round(statistics.median(wake), 2),
        "wake_kinds": {k: ka[k] - kb.get(k, 0) for k in ka if ka[k] - kb.get(k, 0)},
        "server_mean_start_s": {k: round((sa[k] - sb.get(k, 0)) / (ka[k] - kb.get(k, 0)), 3)
                                for k in ka if ka[k] - kb.get(k, 0)},
    }, indent=2))


if __name__ == "__main__":
    main()
