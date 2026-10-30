"""Credits: double-entry-ish ledger + cached balance on workspace; usage metering (ARCHITECTURE §7/§14).

1 credit = $0.01 of model cost. oa-mock costs 1 credit per 1k tokens (min 1) so the meter is visible in demos.
"""
from __future__ import annotations

import math
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from oa.core.events import bus
from oa.db import models as m
from oa.db.session import SessionLocal, session_scope

PLANS = [
    {"id": "free", "name": "Free", "price_usd": 0, "monthly_credits": 500,
     "features": ["Lite & team mode", "Public projects", "Community support"]},
    {"id": "pro", "name": "Pro", "price_usd": 20, "monthly_credits": 5000,
     "features": ["Private projects", "Custom domains", "Advanced models", "Remove badge"]},
    {"id": "max", "name": "Max", "price_usd": 100, "monthly_credits": 30000,
     "features": ["Everything in Pro", "Priority sandboxes", "Race mode", "Team workspace"]},
]
PACKS = {"pack_1000": {"name": "1,000 credits", "price_usd": 10, "credits": 1000},
         "pack_5000": {"name": "5,000 credits", "price_usd": 45, "credits": 5000}}

# USD per 1M tokens (input, output)
PRICES: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-opus-4-1": (15.0, 75.0),
    "gpt-5": (1.25, 10.0),
    "gpt-4o": (2.5, 10.0),
    "deepseek-chat": (0.27, 1.1),
    "kimi-k2": (0.6, 2.5),
}
DEFAULT_PRICE = (1.0, 4.0)


def plan_by_id(pid: str) -> dict | None:
    return next((p for p in PLANS if p["id"] == pid), None)


def compute(model: str, input_tokens: int, output_tokens: int) -> tuple[float, int]:
    if model == "oa-mock":
        return 0.0, max(1, math.ceil((input_tokens + output_tokens) / 1000))
    pin, pout = PRICES.get(model, DEFAULT_PRICE)
    cost = input_tokens / 1e6 * pin + output_tokens / 1e6 * pout
    return cost, max(1, math.ceil(round(cost * 100, 6)))


async def add_ledger(
    session: AsyncSession, workspace_id: int, delta: int, reason: str, *, user_id: int | None = None,
    chat_id: str | None = None, ref: str | None = None,
) -> int:
    session.add(m.CreditLedger(workspace_id=workspace_id, user_id=user_id, chat_id=chat_id, delta=delta,
                               reason=reason, ref=ref))
    ws = await session.get(m.Workspace, workspace_id)
    ws.credits = int(ws.credits or 0) + delta
    await session.flush()
    return ws.credits


async def notify_balance(workspace_id: int) -> None:
    async with SessionLocal() as s:
        ws = await s.get(m.Workspace, workspace_id)
        if ws is None:
            return
        members = (await s.execute(select(m.WorkspaceMember.user_id).where(
            m.WorkspaceMember.workspace_id == workspace_id))).scalars().all()
    for uid in members:
        await bus.emit_user(uid, "quota:update", {"credits": ws.credits, "workspace_id": workspace_id})


async def has_credits(chat_id: str) -> bool:
    async with SessionLocal() as s:
        chat = await s.get(m.Chat, chat_id)
        if chat is None:
            return False
        ws = await s.get(m.Workspace, chat.workspace_id)
        return bool(ws and ws.credits > 0)


async def charge_usage(
    chat_id: str, run_id: int | None, role: str, model: str, input_tokens: int, output_tokens: int
) -> None:
    cost, credits = compute(model, input_tokens, output_tokens)
    async with session_scope() as s:
        chat = await s.get(m.Chat, chat_id)
        if chat is None:
            return
        s.add(m.UsageEvent(chat_id=chat_id, run_id=run_id, role=role, model=model, input_tokens=input_tokens,
                           output_tokens=output_tokens, cost_usd=cost, credits=credits))
        await add_ledger(s, chat.workspace_id, -credits, "llm", chat_id=chat_id, ref=f"run:{run_id}" if run_id else None)
        wid = chat.workspace_id
    await notify_balance(wid)


async def balance(session: AsyncSession, ws: m.Workspace) -> dict:
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    used = (
        await session.execute(
            select(func.coalesce(func.sum(m.CreditLedger.delta), 0)).where(
                m.CreditLedger.workspace_id == ws.id, m.CreditLedger.delta < 0, m.CreditLedger.created_at >= start
            )
        )
    ).scalar()
    plan = plan_by_id(ws.plan) or PLANS[0]
    return {"credits": int(ws.credits or 0), "plan": ws.plan, "monthly_quota": plan["monthly_credits"],
            "used_this_month": -int(used or 0)}


async def fulfill(session: AsyncSession, payment: m.Payment) -> None:
    if payment.status == "paid":
        return
    payment.status = "paid"
    if payment.kind == "plan":
        plan = plan_by_id(payment.item)
        ws = await session.get(m.Workspace, payment.workspace_id)
        ws.plan = payment.item
        user = await session.get(m.User, payment.user_id)
        if user:
            user.plan = payment.item
        await add_ledger(session, ws.id, plan["monthly_credits"], "subscription", user_id=payment.user_id, ref=payment.id)
    else:
        pack = PACKS[payment.item]
        await add_ledger(session, payment.workspace_id, pack["credits"], "purchase", user_id=payment.user_id,
                         ref=payment.id)
    await session.commit()
    await notify_balance(payment.workspace_id)
