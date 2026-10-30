"""Passwords (argon2), JWTs, symmetric encryption for secrets/tokens, HMAC-signed values."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets as _secrets
import time
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

from oa.config import settings

_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return _ph.verify(hashed, pw)
    except (VerificationError, InvalidHashError):
        return False


# ------------------------------------------------------------------ JWT
def _key(secret: str | None) -> str:
    # stretch any configured secret to a 256-bit HMAC key
    return hashlib.sha256((secret or settings.jwt_secret).encode()).hexdigest()


def _encode(payload: dict[str, Any], ttl: int, secret: str | None = None) -> str:
    now = int(time.time())
    return jwt.encode({**payload, "iat": now, "exp": now + ttl}, _key(secret), algorithm="HS256")


def decode(token: str, typ: str | None = None, secret: str | None = None) -> dict[str, Any] | None:
    try:
        data = jwt.decode(token, _key(secret), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if typ and data.get("typ") != typ:
        return None
    return data


def access_token(user_id: int, email: str) -> str:
    return _encode({"user_id": user_id, "email": email, "typ": "access"}, settings.access_token_minutes * 60)


def refresh_token(user_id: int, token_version: int) -> str:
    return _encode({"user_id": user_id, "tv": token_version, "typ": "refresh"}, settings.refresh_token_days * 86400)


def app_secret(chat_id: str) -> str:
    return f"{settings.jwt_secret}:app:{chat_id}"


def app_token(chat_id: str, env: str, app_user_id: int, email: str) -> str:
    return _encode(
        {"app_user_id": app_user_id, "email": email, "chat_id": chat_id, "env": env, "typ": "app"},
        30 * 86400,
        app_secret(chat_id),
    )


# ------------------------------------------------------------------ encryption
def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
    return Fernet(key)


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


# ------------------------------------------------------------------ signed values / random tokens
def sign(value: str, ttl: int) -> str:
    exp = int(time.time()) + ttl
    msg = f"{value}.{exp}"
    sig = hmac.new(settings.internal_token.encode() + settings.jwt_secret.encode(), msg.encode(), hashlib.sha256)
    return f"{msg}.{sig.hexdigest()[:32]}"


def unsign(signed: str) -> str | None:
    try:
        value, exp, sig = signed.rsplit(".", 2)
    except ValueError:
        return None
    msg = f"{value}.{exp}"
    good = hmac.new(settings.internal_token.encode() + settings.jwt_secret.encode(), msg.encode(), hashlib.sha256)
    if not hmac.compare_digest(good.hexdigest()[:32], sig):
        return None
    if int(exp) < time.time():
        return None
    return value


def random_token(n: int = 24) -> str:
    return _secrets.token_urlsafe(n)
