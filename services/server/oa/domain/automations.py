"""Automations (定时任务): cron-scheduled prompts posted into a project's chat.

Cron: classic 5 fields `minute hour day-of-month month day-of-week` with `*`, lists, ranges, steps
(`*/15`, `1-5`, `MON-FRI`, `0,30`) and the macros @hourly/@daily/@weekly/@monthly. Day-of-month and
day-of-week follow Vixie-cron semantics (either matches when both are restricted). Times are evaluated in
the automation's IANA timezone and stored in UTC.

Firing claims a due row with an optimistic compare-and-set on `next_run_at`, so several api replicas and
workers can run `tick()` concurrently without double-sending.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select, update

from oa.core.events import bus
from oa.db import models as m
from oa.db.session import session_scope

log = logging.getLogger("oa.automations")

MACROS = {"@hourly": "0 * * * *", "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@weekly": "0 0 * * 0",
          "@monthly": "0 0 1 * *", "@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *"}
MONTHS = {n: i for i, n in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV",
                                      "DEC"], 1)}
DAYS = {n: i for i, n in enumerate(["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"])}
PRESETS = [
    {"label": "Every hour", "cron": "0 * * * *"},
    {"label": "Every day at 09:00", "cron": "0 9 * * *"},
    {"label": "Weekdays at 09:00", "cron": "0 9 * * 1-5"},
    {"label": "Every Monday at 09:00", "cron": "0 9 * * 1"},
    {"label": "First day of the month", "cron": "0 9 1 * *"},
]


class CronError(ValueError):
    pass


@dataclass(frozen=True)
class Cron:
    minutes: frozenset[int]
    hours: frozenset[int]
    dom: frozenset[int]
    months: frozenset[int]
    dow: frozenset[int]
    dom_any: bool
    dow_any: bool

    def matches(self, t: datetime) -> bool:
        if t.minute not in self.minutes or t.hour not in self.hours or t.month not in self.months:
            return False
        dom_ok = t.day in self.dom
        dow_ok = (t.isoweekday() % 7) in self.dow
        if self.dom_any or self.dow_any:
            return dom_ok and dow_ok
        return dom_ok or dow_ok


def _field(expr: str, lo: int, hi: int, names: dict[str, int] | None = None) -> frozenset[int]:
    out: set[int] = set()
    for part in expr.split(","):
        part = part.strip().upper()
        if not part:
            raise CronError("empty list item")
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            if not s.isdigit() or int(s) == 0:
                raise CronError(f"bad step {s!r}")
            step = int(s)

        def val(x: str) -> int:
            if names and x in names:
                return names[x]
            if not x.isdigit():
                raise CronError(f"bad value {x!r}")
            return int(x)

        if part in ("*", "?"):
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            a, b = val(x), val(y)
        else:
            a = val(part)
            b = hi if step > 1 else a
        if not (lo <= a <= hi and lo <= b <= hi) or a > b:
            raise CronError(f"value out of range {lo}-{hi}: {part}")
        out.update(range(a, b + 1, step))
    if hi == 7 and 7 in out:  # day-of-week: 7 is an alias for Sunday (0)
        out.discard(7)
        out.add(0)
    return frozenset(out)


def parse_cron(expr: str) -> Cron:
    expr = MACROS.get(expr.strip().lower(), expr.strip())
    parts = expr.split()
    if len(parts) != 5:
        raise CronError("cron needs 5 fields: minute hour day-of-month month day-of-week")
    mi, h, dom, mon, dow = parts
    return Cron(_field(mi, 0, 59), _field(h, 0, 23), _field(dom, 1, 31), _field(mon, 1, 12, MONTHS),
                _field(dow, 0, 7, DAYS), dom in ("*", "?"), dow in ("*", "?"))


def tz_of(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise CronError(f"unknown timezone {name!r}") from e


def next_fire(expr: str, tz: str = "UTC", after: datetime | None = None) -> datetime:
    """Next UTC instant strictly after `after` (default now) at which the cron matches in `tz`."""
    cron = parse_cron(expr)
    zone = tz_of(tz)
    t = (after or datetime.now(UTC)).astimezone(zone).replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = t + timedelta(days=366 * 4)
    while t < limit:
        if t.month not in cron.months:
            t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
            continue
        if not _day_ok(cron, t):
            t = (t + timedelta(days=1)).replace(hour=0, minute=0)
            continue
        if t.hour not in cron.hours:
            t = (t + timedelta(hours=1)).replace(minute=0)
            continue
        if t.minute in cron.minutes:
            return t.astimezone(UTC)
        t += timedelta(minutes=1)
    raise CronError("cron never fires")


def _day_ok(cron: Cron, t: datetime) -> bool:
    dom_ok = t.day in cron.dom
    dow_ok = (t.isoweekday() % 7) in cron.dow
    if cron.dom_any or cron.dow_any:
        return dom_ok and dow_ok
    return dom_ok or dow_ok


def describe(expr: str) -> str:
    for p in PRESETS:
        if p["cron"] == expr.strip():
            return p["label"]
    return expr


# ---------------------------------------------------------------- serialization
def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).isoformat()


def serialize(a: m.Automation, chat_title: str | None = None) -> dict[str, Any]:
    return {
        "id": a.id, "chat_id": a.chat_id, "chat_title": chat_title, "workspace_id": a.workspace_id,
        "user_id": a.user_id, "name": a.name, "prompt": a.prompt, "cron": a.cron, "schedule": describe(a.cron),
        "timezone": a.timezone, "enabled": a.enabled, "last_run_at": _iso(a.last_run_at),
        "next_run_at": _iso(a.next_run_at), "last_status": a.last_status, "last_message_id": a.last_message_id,
        "run_count": a.run_count, "created_at": _iso(a.created_at), "updated_at": _iso(a.updated_at),
    }


# ---------------------------------------------------------------- firing
async def fire(automation_id: int, *, manual: bool = False) -> dict[str, Any]:
    """Post the automation's prompt into its chat (queued when a run is active). Returns the updated row."""
    from oa.domain.messages import post_user_message

    async with session_scope() as s:
        a = await s.get(m.Automation, automation_id)
        if a is None:
            raise LookupError("automation not found")
        chat = await s.get(m.Chat, a.chat_id)
        user = await s.get(m.User, a.user_id)
        status, mid = "failed", None
        if chat is None or chat.deleted_at is not None or user is None:
            a.enabled = False
        else:
            try:
                body = {"content": [{"insert": a.prompt}],
                        "metadata": {"automation": {"id": a.id, "name": a.name, "manual": manual}}}
                msg, queued, _item = await post_user_message(s, chat, user, body)
                status, mid = ("queued" if queued else "sent"), (msg.id if msg else None)
            except Exception:
                log.exception("automation %s failed to post", automation_id)
        a.last_run_at = datetime.now(UTC)
        a.last_status = status
        a.last_message_id = mid or a.last_message_id
        a.run_count = (a.run_count or 0) + 1
        out = serialize(a, chat.title if chat else None)
        chat_id = a.chat_id
    await bus.emit(chat_id, "automation:update", {"automation": out})
    return out


async def tick(now: datetime | None = None) -> list[int]:
    """Fire every enabled automation whose next_run_at has passed. Returns fired ids."""
    now = now or datetime.now(UTC)
    async with session_scope() as s:
        due = (await s.execute(select(m.Automation.id, m.Automation.next_run_at, m.Automation.cron,
                                      m.Automation.timezone)
                               .where(m.Automation.enabled.is_(True), m.Automation.next_run_at.is_not(None),
                                      m.Automation.next_run_at <= now))).all()
    fired: list[int] = []
    for aid, prev, cron, tz in due:
        try:
            nxt = next_fire(cron, tz, now)
        except CronError:
            nxt = None
        async with session_scope() as s:
            # compare-and-set claim: only one process advances next_run_at from `prev`
            res = await s.execute(update(m.Automation).where(m.Automation.id == aid, m.Automation.next_run_at == prev)
                                  .values(next_run_at=nxt, enabled=nxt is not None))
            claimed = res.rowcount == 1
        if claimed:
            await fire(aid)
            fired.append(aid)
    return fired


async def scheduler_loop(interval: float = 20.0) -> None:
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("automation tick failed")
        await asyncio.sleep(interval)
