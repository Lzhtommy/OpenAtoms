"""Credits balance/ledger, plans, Stripe Checkout (or dev mock), Stripe webhook."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from oa.config import settings
from oa.core import security
from oa.core.deps import WorkspaceCtx, current_user, current_workspace, has_role
from oa.core.envelope import bad_request, forbidden, not_found, ok
from oa.db import models as m
from oa.db.session import get_session
from oa.domain import billing
from oa.domain import serializers as ser

router = APIRouter(tags=["billing"])


@router.get("/transaction/balance")
async def balance(ctx: WorkspaceCtx = Depends(current_workspace), session: AsyncSession = Depends(get_session)):
    return ok(await billing.balance(session, ctx.workspace))


@router.get("/transaction/ledger")
async def ledger(cur_page: int = Query(1, ge=1), page_num: int = Query(20, ge=1, le=100),
                 ctx: WorkspaceCtx = Depends(current_workspace), session: AsyncSession = Depends(get_session)):
    q = select(m.CreditLedger).where(m.CreditLedger.workspace_id == ctx.workspace.id)
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar() or 0
    rows = (await session.execute(q.order_by(m.CreditLedger.id.desc()).offset((cur_page - 1) * page_num).limit(page_num))).scalars()
    items = [{"id": r.id, "delta": r.delta, "reason": r.reason, "ref": r.ref, "chat_id": r.chat_id,
              "created_at": ser.iso(r.created_at)} for r in rows]
    return ok({"items": items, "total": total, "has_more": cur_page * page_num < total})


@router.get("/billing/plans")
async def plans():
    return ok(billing.PLANS)


@router.get("/billing/packs")
async def packs():
    return ok([{"id": k, **v} for k, v in billing.PACKS.items()])


class CheckoutBody(BaseModel):
    plan: str | None = None
    pack: str | None = None


@router.post("/billing/checkout")
async def checkout(body: CheckoutBody, ctx: WorkspaceCtx = Depends(current_workspace), user: m.User = Depends(current_user),
                   session: AsyncSession = Depends(get_session)):
    if not has_role(ctx.role, "admin"):
        raise forbidden("Only workspace admins can purchase")
    if body.plan:
        plan = billing.plan_by_id(body.plan)
        if plan is None or plan["price_usd"] <= 0:
            raise bad_request("invalid plan")
        kind, item, name, price = "plan", plan["id"], f"OpenAtoms {plan['name']}", plan["price_usd"]
    elif body.pack and body.pack in billing.PACKS:
        pk = billing.PACKS[body.pack]
        kind, item, name, price = "pack", body.pack, f"OpenAtoms {pk['name']}", pk["price_usd"]
    else:
        raise bad_request("plan or pack required")

    if settings.stripe_secret:
        form = {
            "mode": "payment",
            "success_url": f"{settings.public_web_url}/settings?tab=billing&paid=1",
            "cancel_url": f"{settings.public_web_url}/pricing",
            "line_items[0][quantity]": "1",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": str(int(price * 100)),
            "line_items[0][price_data][product_data][name]": name,
            "client_reference_id": str(ctx.workspace.id),
            "customer_email": user.email,
            "metadata[workspace_id]": str(ctx.workspace.id),
            "metadata[kind]": kind,
            "metadata[item]": item,
        }
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{settings.stripe_api_url.rstrip('/')}/v1/checkout/sessions", data=form, auth=(settings.stripe_secret, ""))
        if r.status_code != 200:
            raise bad_request("Stripe checkout failed")
        sess = r.json()
        session.add(m.Payment(id=sess["id"], workspace_id=ctx.workspace.id, user_id=user.id, kind=kind, item=item,
                              amount_usd=price))
        await session.commit()
        return ok({"url": sess["url"], "session": sess["id"]})

    sid = f"mock_{security.random_token(12)}"
    session.add(m.Payment(id=sid, workspace_id=ctx.workspace.id, user_id=user.id, kind=kind, item=item, amount_usd=price))
    await session.commit()
    return ok({"url": f"{settings.public_web_url}/billing/mock?session={sid}", "session": sid, "mock": True})


class MockBody(BaseModel):
    session: str


@router.get("/billing/mock/{sid}")
async def mock_info(sid: str, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    p = await session.get(m.Payment, sid)
    if p is None or not sid.startswith("mock_"):
        raise not_found("Checkout session not found")
    return ok({"session": p.id, "kind": p.kind, "item": p.item, "amount_usd": p.amount_usd, "status": p.status})


@router.post("/billing/mock/complete")
async def mock_complete(body: MockBody, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    if settings.stripe_secret or not body.session.startswith("mock_"):
        raise forbidden("Mock checkout is disabled when Stripe is configured")
    p = await session.get(m.Payment, body.session)
    if p is None or p.user_id != user.id:
        raise not_found("Checkout session not found")
    await billing.fulfill(session, p)
    ws = await session.get(m.Workspace, p.workspace_id)
    return ok(await billing.balance(session, ws))


def verify_stripe(payload: bytes, header: str, secret: str, tolerance: int = 300) -> bool:
    parts = dict(kv.split("=", 1) for kv in header.split(",") if "=" in kv)
    try:
        ts = int(parts.get("t", "0"))
    except ValueError:
        return False
    sigs = [v for k, v in (kv.split("=", 1) for kv in header.split(",") if "=" in kv) if k == "v1"]
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return abs(time.time() - ts) <= tolerance and any(hmac.compare_digest(expected, s) for s in sigs)


@router.post("/billing/webhook")
async def webhook(request: Request, session: AsyncSession = Depends(get_session)):
    payload = await request.body()
    if not settings.stripe_webhook_secret or not verify_stripe(payload, request.headers.get("stripe-signature", ""),
                                                                settings.stripe_webhook_secret):
        raise bad_request("invalid signature")
    event = json.loads(payload)
    if event.get("type") in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        obj = event["data"]["object"]
        p = await session.get(m.Payment, obj["id"])
        if p is None:
            md = obj.get("metadata") or {}
            if not md.get("workspace_id"):
                return ok({"ignored": True})
            ws = await session.get(m.Workspace, int(md["workspace_id"]))
            p = m.Payment(id=obj["id"], workspace_id=ws.id, user_id=ws.owner_id, kind=md.get("kind", "pack"),
                          item=md.get("item", "pack_1000"), amount_usd=(obj.get("amount_total") or 0) / 100)
            session.add(p)
            await session.flush()
        if obj.get("payment_status") in (None, "paid", "no_payment_required"):
            await billing.fulfill(session, p)
    return ok({"received": True})
