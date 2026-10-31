"""/user/*: register, login, refresh, logout, profile, password reset, email verification, Google OAuth."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from oa.config import settings
from oa.core import security
from oa.core.deps import current_user
from oa.core.envelope import BAD_REQUEST, CONFLICT, ApiError, bad_request, ok, unauthorized
from oa.core.mail import send_mail
from oa.db import models as m
from oa.db.session import get_session
from oa.domain import billing
from oa.domain import serializers as ser

router = APIRouter(prefix="/user", tags=["user"])
REFRESH_COOKIE = "oa_refresh"


class RegisterBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=200)
    name: str | None = None


class LoginBody(BaseModel):
    email: EmailStr
    password: str


def set_refresh(resp: Response, user: m.User) -> None:
    resp.set_cookie(
        REFRESH_COOKIE, security.refresh_token(user.id, user.token_version or 0),
        max_age=settings.refresh_token_days * 86400, httponly=True, samesite="lax",
        secure=settings.public_web_url.startswith("https"), path="/api/v1/user",
    )


async def create_one_time(session: AsyncSession, kind: str, user_id: int | None, ttl: timedelta, data: dict | None = None) -> str:
    tok = security.random_token()
    session.add(m.AuthToken(token=tok, kind=kind, user_id=user_id, data=data or {},
                            expires_at=datetime.now(UTC) + ttl))
    await session.flush()
    return tok


async def consume_one_time(session: AsyncSession, token: str, kind: str) -> m.AuthToken:
    row = await session.get(m.AuthToken, token)
    now = datetime.now(UTC)
    if row is None or row.kind != kind or row.used_at is not None or _aware(row.expires_at) < now:
        raise bad_request("Invalid or expired token")
    row.used_at = now
    return row


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def provision_user(session: AsyncSession, email: str, name: str, password: str | None = None,
                         google_sub: str | None = None, verified: bool = False) -> m.User:
    """Create a user plus personal workspace and signup credits."""
    user = m.User(email=email.lower(), name=name or email.split("@")[0],
                  password_hash=security.hash_password(password) if password else None,
                  google_sub=google_sub, email_verified=verified)
    session.add(user)
    await session.flush()
    ws = m.Workspace(name=f"{user.name}'s Workspace", owner_id=user.id, credits=0)
    session.add(ws)
    await session.flush()
    session.add(m.WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="owner"))
    user.current_workspace_id = ws.id
    await billing.add_ledger(session, ws.id, settings.signup_credits, "signup", user_id=user.id)
    await session.flush()
    return user


async def _send_verification(session: AsyncSession, user: m.User) -> str:
    tok = await create_one_time(session, "verify_email", user.id, timedelta(days=3))
    await session.commit()
    await send_mail(user.email, "Verify your OpenAtoms email",
                    f"Welcome to OpenAtoms!\n\nVerify your email: {settings.public_web_url}/verify-email?token={tok}\n")
    return tok


def auth_payload(user: m.User) -> dict:
    return {"access_token": security.access_token(user.id, user.email), "user": ser.user(user)}


@router.post("/register")
async def register(body: RegisterBody, response: Response, session: AsyncSession = Depends(get_session)):
    exists = (await session.execute(select(m.User).where(func.lower(m.User.email) == body.email.lower()))).scalar_one_or_none()
    if exists:
        raise ApiError(CONFLICT, "Email already registered")
    user = await provision_user(session, body.email, body.name or "", body.password)
    await session.commit()
    await _send_verification(session, user)
    set_refresh(response, user)
    return ok(auth_payload(user))


@router.post("/login")
async def login(body: LoginBody, response: Response, session: AsyncSession = Depends(get_session)):
    user = (await session.execute(select(m.User).where(func.lower(m.User.email) == body.email.lower()))).scalar_one_or_none()
    if user is None or not security.verify_password(body.password, user.password_hash):
        raise ApiError(BAD_REQUEST, "Incorrect email or password", status=401)
    set_refresh(response, user)
    return ok(auth_payload(user))


@router.post("/refresh")
async def refresh(request: Request, response: Response, session: AsyncSession = Depends(get_session)):
    tok = request.cookies.get(REFRESH_COOKIE)
    data = security.decode(tok, "refresh") if tok else None
    user = await session.get(m.User, data["user_id"]) if data else None
    if user is None or int(data.get("tv", 0)) != int(user.token_version or 0):
        raise unauthorized("Refresh token invalid")
    set_refresh(response, user)
    return ok({"access_token": security.access_token(user.id, user.email)})


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/user")
    return ok(None)


@router.get("/detail")
async def detail(user: m.User = Depends(current_user)):
    return ok(ser.user(user))


class ProfileBody(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    avatar: str | None = Field(default=None, max_length=500)


@router.patch("/detail")
async def update_detail(body: ProfileBody, user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    u = await session.get(m.User, user.id)
    if body.name is not None:
        u.name = body.name
    if body.avatar is not None:
        u.avatar = body.avatar
    await session.commit()
    return ok(ser.user(u))


class ForgotBody(BaseModel):
    email: EmailStr


@router.post("/pwd/forgot")
async def pwd_forgot(body: ForgotBody, session: AsyncSession = Depends(get_session)):
    user = (await session.execute(select(m.User).where(func.lower(m.User.email) == body.email.lower()))).scalar_one_or_none()
    data: dict = {}
    if user is not None:
        tok = await create_one_time(session, "pwd_reset", user.id, timedelta(hours=1))
        await session.commit()
        await send_mail(user.email, "Reset your OpenAtoms password",
                        f"Reset your password: {settings.public_web_url}/password/reset?token={tok}\n"
                        "This link expires in 1 hour.")
        if settings.dev_expose_tokens:
            data["dev_token"] = tok
    return ok(data)


class ResetBody(BaseModel):
    token: str
    password: str = Field(min_length=6, max_length=200)


@router.post("/pwd/reset")
async def pwd_reset(body: ResetBody, session: AsyncSession = Depends(get_session)):
    row = await consume_one_time(session, body.token, "pwd_reset")
    user = await session.get(m.User, row.user_id)
    user.password_hash = security.hash_password(body.password)
    user.token_version = (user.token_version or 0) + 1  # revoke refresh tokens
    await session.commit()
    return ok(None)


class TokenBody(BaseModel):
    token: str


@router.post("/activation")
async def activation(body: TokenBody, session: AsyncSession = Depends(get_session)):
    row = await consume_one_time(session, body.token, "verify_email")
    user = await session.get(m.User, row.user_id)
    user.email_verified = True
    await session.commit()
    return ok(ser.user(user))


@router.post("/activation/resend")
async def activation_resend(user: m.User = Depends(current_user), session: AsyncSession = Depends(get_session)):
    if not user.email_verified:
        await _send_verification(session, await session.get(m.User, user.id))
    return ok(None)


# ------------------------------------------------------------------ Google OAuth
def _google_redirect() -> str:
    return f"{settings.public_web_url}/api/v1/user/google/callback"


@router.get("/google/login")
async def google_login(session: AsyncSession = Depends(get_session)):
    if not settings.google_client_id:
        raise bad_request("Google login is not configured")
    state = await create_one_time(session, "oauth_state", None, timedelta(minutes=10), {"provider": "google"})
    await session.commit()
    q = urlencode({"client_id": settings.google_client_id, "redirect_uri": _google_redirect(), "response_type": "code",
                   "scope": "openid email profile", "state": state, "prompt": "select_account"})
    return ok({"url": f"{settings.google_auth_url}?{q}"})


@router.get("/google/callback")
async def google_callback(code: str, state: str, session: AsyncSession = Depends(get_session)):
    if not settings.google_client_id:
        raise bad_request("Google login is not configured")
    await consume_one_time(session, state, "oauth_state")
    async with httpx.AsyncClient(timeout=15) as c:
        tr = await c.post(settings.google_token_url, data={
            "code": code, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
            "redirect_uri": _google_redirect(), "grant_type": "authorization_code"})
        if tr.status_code != 200:
            raise bad_request("Google token exchange failed")
        info = (await c.get(settings.google_userinfo_url,
                            headers={"Authorization": f"Bearer {tr.json()['access_token']}"})).json()
    sub, email = info.get("sub"), (info.get("email") or "").lower()
    if not sub or not email:
        raise bad_request("Google account has no email")
    user = (await session.execute(select(m.User).where(m.User.google_sub == sub))).scalar_one_or_none()
    if user is None:
        user = (await session.execute(select(m.User).where(func.lower(m.User.email) == email))).scalar_one_or_none()
        if user is not None and not info.get("email_verified"):
            # linking by email is only safe when Google vouches for it; otherwise anyone could claim the address
            raise bad_request("This Google account's email is not verified; sign in with your password instead")
        if user is not None:
            user.google_sub = sub
            user.email_verified = True
        else:
            user = await provision_user(session, email, info.get("name") or "", google_sub=sub,
                                        verified=bool(info.get("email_verified")))
            user.avatar = info.get("picture")
    await session.commit()
    resp = RedirectResponse(f"{settings.public_web_url}/oauth/google?{urlencode({'token': security.access_token(user.id, user.email)})}")
    set_refresh(resp, user)
    return resp
