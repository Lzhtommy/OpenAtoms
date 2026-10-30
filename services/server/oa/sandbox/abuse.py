"""Sandbox abuse monitor (PLAN P8 "滥用检测（挖矿进程、外发扫描）").

Every OA_ABUSE_INTERVAL seconds (default 60) each running *isolated* sandbox (docker/k8s; the local provider
shares the host and is dev-only) runs a small /proc scanner through sandboxd /exec and the monitor acts on:

  miner     : known miner binaries / stratum URLs in argv              → kill process, chat error, metric
  cpu_hog   : a non-allowlisted process above OA_ABUSE_CPU (default 90%) of one core, sustained over two scans
              and older than OA_ABUSE_MIN_AGE seconds (default 600)    → kill process
  port_scan : more than OA_ABUSE_SYN (default 64) outbound SYN_SENT sockets / >OA_ABUSE_DESTS distinct
              destinations at once                                      → stop the sandbox (needs a human)

Each action emits `chat_error:create` to the chat, logs a structured security event and increments
oa_abuse_actions_total{reason,action}.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

from sqlalchemy import select

from oa.config import settings
from oa.core.events import bus
from oa.core.observability import ABUSE_ACTIONS
from oa.db.models import Sandbox
from oa.db.session import SessionLocal
from oa.sandbox.client import SandboxClient

log = logging.getLogger("oa.security")

MINER_RE = re.compile(r"(xmrig|xmr-stak|minerd|cpuminer|ethminer|nbminer|t-rex|lolminer|phoenixminer|cgminer|"
                      r"bfgminer|stratum\+(tcp|ssl)://|cryptonight|randomx)", re.I)
# dev tooling that legitimately burns CPU for a while (builds, type-checking, installs, the kernel)
CPU_ALLOW_RE = re.compile(r"(^|/)(node|esbuild|vite|tsc|pnpm|npm|bun|python3?|uv|git|sandboxd)(\s|$)")

SCANNER = r"""
import os, json, time
HZ = os.sysconf('SC_CLK_TCK')
def snap():
    out = {}
    for pid in os.listdir('/proc'):
        if not pid.isdigit():
            continue
        try:
            with open(f'/proc/{pid}/stat') as f:
                st = f.read()
            with open(f'/proc/{pid}/cmdline', 'rb') as f:
                cmd = f.read().replace(b'\0', b' ').decode(errors='replace').strip()
        except Exception:
            continue
        rest = st[st.rfind(')') + 2:].split()
        out[int(pid)] = (int(rest[11]) + int(rest[12]), int(rest[19]), cmd or st[st.find('(') + 1:st.rfind(')')])
    return out
a = snap(); time.sleep(1.0); b = snap()
with open('/proc/uptime') as f:
    up = float(f.read().split()[0])
procs = []
for pid, (t1, start, cmd) in b.items():
    t0 = a.get(pid, (t1,))[0]
    procs.append({'pid': pid, 'cpu': round(100.0 * (t1 - t0) / HZ, 1), 'age': round(up - start / HZ), 'cmd': cmd[:300]})
syn, dests = 0, set()
for fn in ('/proc/net/tcp', '/proc/net/tcp6'):
    try:
        with open(fn) as f:
            next(f)
            for line in f:
                p = line.split()
                if p[3] == '02':
                    syn += 1; dests.add(p[2])
    except Exception:
        pass
print(json.dumps({'self': os.getpid(), 'procs': procs, 'syn_sent': syn, 'syn_dests': len(dests)}))
"""

_hot: dict[tuple[str, int], int] = {}  # (chat, pid) → consecutive hot scans


def _thresholds() -> dict[str, float]:
    e = os.environ.get
    return {"cpu": float(e("OA_ABUSE_CPU", "90")), "min_age": float(e("OA_ABUSE_MIN_AGE", "600")),
            "syn": float(e("OA_ABUSE_SYN", "64")), "dests": float(e("OA_ABUSE_DESTS", "32"))}


def evaluate(chat_id: str, scan: dict[str, Any]) -> list[dict[str, Any]]:
    """Pure decision function (unit-tested): returns actions [{reason, action, pid?, detail}]."""
    th = _thresholds()
    actions: list[dict[str, Any]] = []
    me = scan.get("self")
    seen: set[int] = set()
    for p in scan.get("procs", []):
        pid, cmd = int(p["pid"]), str(p.get("cmd", ""))
        if pid in (1, me) or "sandboxd" in cmd.split(" ")[0]:
            continue
        seen.add(pid)
        if MINER_RE.search(cmd):
            actions.append({"reason": "miner", "action": "kill", "pid": pid, "detail": cmd[:160]})
            continue
        key = (chat_id, pid)
        if p.get("cpu", 0) >= th["cpu"] and p.get("age", 0) >= th["min_age"] and not CPU_ALLOW_RE.search(cmd):
            _hot[key] = _hot.get(key, 0) + 1
            if _hot[key] >= 2:
                actions.append({"reason": "cpu_hog", "action": "kill", "pid": pid,
                                "detail": f"{p['cpu']}% CPU for {p['age']}s: {cmd[:120]}"})
        else:
            _hot.pop(key, None)
    for key in [k for k in _hot if k[0] == chat_id and k[1] not in seen]:
        _hot.pop(key, None)
    if scan.get("syn_sent", 0) > th["syn"] or scan.get("syn_dests", 0) > th["dests"]:
        actions.append({"reason": "port_scan", "action": "stop",
                        "detail": f"{scan['syn_sent']} half-open outbound connections to {scan['syn_dests']} hosts"})
    return actions


async def scan_sandbox(chat_id: str, client: SandboxClient) -> list[dict[str, Any]]:
    # piped via stdin: nothing is written into the user's workspace
    code, out = await client.run(f"python3 - <<'OA_SCAN'\n{SCANNER}\nOA_SCAN", timeout_ms=15000)
    if code != 0:
        raise RuntimeError(f"scanner failed: {out[-300:]}")
    line = next((ln for ln in reversed(out.splitlines()) if ln.startswith("{")), "{}")
    return evaluate(chat_id, json.loads(line))


async def _act(chat_id: str, client: SandboxClient, a: dict[str, Any]) -> None:
    ABUSE_ACTIONS.labels(a["reason"], a["action"]).inc()
    log.warning(json.dumps({"event": "sandbox_abuse", "chat_id": chat_id, **a}))
    if a["action"] == "kill" and a.get("pid"):
        await client.run(f"kill -9 {int(a['pid'])} 2>/dev/null || true", timeout_ms=5000)
        msg = f"Stopped a process that violates the sandbox policy ({a['reason']}): {a['detail']}"
    else:
        from oa.sandbox import manager

        await manager.stop(chat_id)
        msg = f"The sandbox was stopped because of suspicious network activity ({a['detail']})."
    await bus.emit(chat_id, "chat_error:create", {"error": {"id": f"abuse-{a['reason']}", "message": msg,
                                                            "code": "sandbox_policy"}})


async def scan_all_once() -> int:
    if settings.sandbox_provider == "local" and os.environ.get("OA_ABUSE_LOCAL") != "1":
        return 0
    async with SessionLocal() as s:
        rows = (await s.execute(select(Sandbox.chat_id, Sandbox.endpoint, Sandbox.token)
                                .where(Sandbox.status == "running"))).all()
    n = 0
    for chat_id, endpoint, token in rows:
        if not endpoint:
            continue
        client = SandboxClient(endpoint, token)
        try:
            for a in await scan_sandbox(chat_id, client):
                await _act(chat_id, client, a)
                n += 1
        except Exception as e:
            log.debug("abuse scan of %s failed: %s", chat_id, e)
        finally:
            await client.aclose()
    return n


async def monitor_loop() -> None:
    interval = float(os.environ.get("OA_ABUSE_INTERVAL", "60"))
    while True:
        try:
            await scan_all_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("abuse monitor iteration failed")
        await asyncio.sleep(interval)
