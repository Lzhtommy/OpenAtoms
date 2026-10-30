"""Prompt moderation hook (PLAN P8 "内容审核").

Pluggable via OA_MODERATION:
  keyword (default) — built-in category patterns + OA_MODERATION_BLOCKLIST (comma-separated extra terms)
  webhook           — POST OA_MODERATION_URL {"text"} → {"flagged": bool, "category"?: str, "reason"?: str}
                      (fails open on errors, after the keyword pass)
  off
The built-in list targets clear abuse of the platform itself (credential phishing kits, malware, crypto-mining,
CSAM), not ordinary app ideas: "a login page" is fine, "a fake PayPal login page to collect passwords" is not.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import httpx

from oa.core.observability import MODERATION_BLOCKS

log = logging.getLogger("oa.moderation")

BLOCKED_CODE = 4451


@dataclass
class Verdict:
    flagged: bool
    category: str = ""
    reason: str = ""


_BRANDS = r"(paypal|apple ?id|icloud|microsoft|office ?365|outlook|gmail|google|facebook|instagram|metamask|coinbase|" \
          r"binance|bank of america|chase|wells fargo|alipay|支付宝|微信|wechat|招商银行|工商银行)"
_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("phishing", re.compile(
        rf"((fake|clone|replica|spoof|仿冒|伪造|钓鱼)\W+(\w+\W+){{0,4}}{_BRANDS}\W+(\w+\W+){{0,4}}(login|sign.?in|登录)|"
        rf"phishing (page|kit|site)|钓鱼(网站|页面)|(steal|harvest|collect|窃取|盗取)\W+(\w+\W+){{0,3}}"
        rf"(passwords?|credentials|seed phrases?|密码|助记词))", re.I)),
    ("malware", re.compile(
        r"(ransomware|keylogger|credential stealer|info.?stealer|botnet|勒索(软件|病毒)|木马程序|键盘记录器|"
        r"(write|build|create|写|做)\W+(\w+\W+){0,3}(virus|worm|trojan|rootkit))", re.I)),
    ("cryptomining", re.compile(r"(xmrig|cryptonight|monero miner|coin ?hive|挖矿(脚本|程序))", re.I)),
    ("csam", re.compile(r"(child (porn|sexual)|underage (sex|nude)|儿童色情)", re.I)),
]


def _extra_terms() -> list[str]:
    raw = os.environ.get("OA_MODERATION_BLOCKLIST", "")
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def keyword_check(text: str) -> Verdict:
    for category, pat in _RULES:
        if pat.search(text):
            return Verdict(True, category, f"matched {category} policy")
    low = text.lower()
    for term in _extra_terms():
        if term in low:
            return Verdict(True, "blocklist", "matched operator blocklist")
    return Verdict(False)


async def moderate(text: str) -> Verdict:
    mode = os.environ.get("OA_MODERATION", "keyword").lower()
    if mode == "off" or not text.strip():
        return Verdict(False)
    v = keyword_check(text)
    if v.flagged or mode != "webhook":
        if v.flagged:
            MODERATION_BLOCKS.labels(v.category).inc()
        return v
    url = os.environ.get("OA_MODERATION_URL")
    if not url:
        return v
    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as c:
            r = await c.post(url, json={"text": text})
            d = r.json()
        if d.get("flagged"):
            MODERATION_BLOCKS.labels(str(d.get("category") or "webhook")).inc()
            return Verdict(True, str(d.get("category") or "webhook"), str(d.get("reason") or "flagged"))
    except Exception as e:  # fail open: availability of the builder beats an unavailable moderation backend
        log.warning("moderation webhook failed: %s", e)
    return Verdict(False)


def delta_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(op.get("insert") for op in content if isinstance(op, dict) and isinstance(op.get("insert"), str))
    return ""
