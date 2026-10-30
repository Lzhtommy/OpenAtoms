"""Outgoing mail: persisted to outbox (DB + data/outbox/*.eml); delivered via SMTP when configured."""
from __future__ import annotations

import asyncio
import logging
import smtplib
from email.message import EmailMessage

from oa.config import settings
from oa.db import models as m
from oa.db.session import session_scope

log = logging.getLogger("oa.mail")


def _build(to: str, subject: str, text: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = settings.mail_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    return msg


def _smtp_send(msg: EmailMessage) -> None:
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as s:
        s.send_message(msg)


async def send_mail(to: str, subject: str, text: str) -> None:
    msg = _build(to, subject, text)
    sent = False
    if settings.smtp_host:
        try:
            await asyncio.to_thread(_smtp_send, msg)
            sent = True
        except Exception:
            log.exception("smtp send failed")
    async with session_scope() as s:
        row = m.OutboxEmail(to=to, subject=subject, body=text, sent=sent)
        s.add(row)
        await s.flush()
        rid = row.id
    outbox = settings.data_dir / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / f"{rid:06d}.eml").write_bytes(bytes(msg))
    log.info("mail to=%s subject=%s (smtp=%s)", to, subject, sent)
