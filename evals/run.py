# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "pyyaml>=6"]
# ///
"""Run evals/cases.yaml against a running stack. `uv run evals/run.py [--model M] [--only id,id]`.
Writes evals/results/<timestamp>.json and prints a pass-rate table."""
from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

import httpx
import yaml

HERE = Path(__file__).parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000/api/v1")
    ap.add_argument("--model", default=None)
    ap.add_argument("--only", default="")
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()
    cases = yaml.safe_load((HERE / "cases.yaml").read_text())
    if a.only:
        cases = [c for c in cases if c["id"] in a.only.split(",")]
    c = httpx.Client(base_url=a.api, timeout=60)

    def call(method: str, path: str, **kw):  # noqa: ANN003, ANN202
        r = c.request(method, path, **kw).json()
        if r["code"] != 0:
            raise RuntimeError(f"{path}: {r}")
        return r["data"]

    tok = call("POST", "/user/register", json={"email": f"eval-{uuid.uuid4().hex[:6]}@x.dev", "password": "pass12345"})
    c.headers["Authorization"] = tok["access_token"]
    results = []
    for case in cases:
        t0 = time.time()
        body = {"title": case["id"], "config": {"agent_mode": case["mode"]},
                "first_message": {"content": [{"insert": case["prompt"]}], "metadata": {"agent_mode": case["mode"]}}}
        if a.model:
            body["llm"] = {"default_model": a.model}
        chat = call("POST", "/chats", json=body)
        cid = chat["chat_id"]
        status = "timeout"
        while time.time() - t0 < a.timeout:
            ch = call("GET", f"/chats/{cid}")
            msgs = call("GET", f"/chats/{cid}/messages")["messages"]
            pending = [m for m in msgs if m["type"] in ("task", "questions_card", "plan_ready")
                       and isinstance(m["content"], dict) and m["content"].get("status") == "pending"]
            for m in pending:  # auto-answer human-in-the-loop cards
                if m["type"] == "task":
                    call("POST", f"/chats/{cid}/messages", json={"content": [{"insert": m["content"]["option"][0]["content"]}],
                                                                 "metadata": {"task_reply": {"message_id": m["id"], "option_index": 0}}})
                elif m["type"] == "questions_card":
                    ans = {q["id"]: (q.get("options") or ["any"])[0] for q in m["content"]["questions"]}
                    call("POST", f"/chats/{cid}/submit-questions", json={"message_id": m["id"], "answers": ans})
                else:
                    call("POST", f"/chats/{cid}/approve-plan")
            if not ch["metadata"]["is_running"] and not pending and time.time() - t0 > 3:
                status = "done"
                break
            time.sleep(2)
        ads = call("GET", f"/chats/{cid}/messages")["action_datas"]
        builds = [x for x in ads if x["command_name"] == "terminal_run" and "build" in json.dumps(x["extra"])]
        build_ok = bool(builds) and builds[-1]["status"] == "success"
        checks = [x for x in ads if x["command_name"] == "checkui_run"]
        ui_ok = not checks or checks[-1]["status"] == "success"
        src = ""
        for f in src_files(call("GET", f"/chats/{cid}/files/tree")):  # expected strings may live anywhere in src/
            try:
                src += call("GET", f"/chats/{cid}/files", params={"path": f}).get("content", "")
            except Exception:  # noqa: BLE001
                pass
        expect_ok = all(e.lower() in src.lower() for e in case.get("expect", []))
        vs = call("GET", f"/chats/{cid}/versions")
        passed = status == "done" and build_ok and ui_ok and expect_ok and any(v["status"] == "ready" for v in vs)
        results.append({"id": case["id"], "passed": passed, "status": status, "build": build_ok, "ui": ui_ok,
                        "expect": expect_ok, "seconds": round(time.time() - t0, 1), "chat_id": cid})
        print(f"{'PASS' if passed else 'FAIL'} {case['id']:<18} {results[-1]}", flush=True)
        try:  # free the sandbox: plans cap concurrent sandboxes, and cases run back to back
            call("POST", f"/chats/{cid}/runtime/stop")
        except Exception:  # noqa: BLE001
            pass
    out = HERE / "results" / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    n = sum(r["passed"] for r in results)
    print(f"\npass rate {n}/{len(results)} → {out}")


def src_files(nodes: list[dict]) -> list[str]:
    out: list[str] = []
    for n in nodes or []:
        if n["type"] == "dir":
            out += src_files(n.get("children") or [])
        elif n["path"].startswith("src/") and n["path"].endswith((".ts", ".tsx", ".js", ".jsx", ".css", ".html")):
            out.append(n["path"])
    return out


if __name__ == "__main__":
    main()
