"""Lazy access to modules owned by the agents workstream (sandbox manager, runner).

Routing every cross-boundary import through these functions keeps platform code importable on its own
and gives tests one seam to substitute fakes.
"""
from __future__ import annotations

import logging
from types import ModuleType

log = logging.getLogger("oa.hooks")


def manager() -> ModuleType | None:
    try:
        from oa.sandbox import manager as m

        return m
    except ImportError:
        log.warning("oa.sandbox.manager unavailable")
        return None


def runner() -> ModuleType | None:
    try:
        from oa.agents import runner as r

        return r
    except ImportError:
        log.warning("oa.agents.runner unavailable")
        return None
