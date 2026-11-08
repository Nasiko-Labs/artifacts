"""Settings. Every one has a built-in default; the environment only overrides it, and an unreadable
override falls back to the default (logged), so the agent never needs env config to run."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

log = logging.getLogger("retro.config")


@dataclass(frozen=True)
class Config:
    planned_grace_hours: int = 24     # added to the cycle by start + this counts as planned
    max_issues: int = 150             # issues kept per cycle after the checks
    max_evidence_issues: int = 10     # spilled issues searched for a reason (highest estimate first)
    reasons_batch: int = 5            # spilled issues per reasons call
    max_teams_offered: int = 10       # options on the "Which team?" card
    max_post_chars: int = 3500        # longer retros are shortened by code before posting
    turn_deadline_secs: float = 300.0
    turn_llm_calls: int = 25          # hard cap per turn
    turn_tool_calls: int = 50         # hard cap per turn

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        base = Config()
        return Config(
            planned_grace_hours=_int(env.get("PLANNED_GRACE_HOURS"), base.planned_grace_hours, 0, 168),
            max_issues=_int(env.get("MAX_ISSUES"), base.max_issues, 1, 300),
            max_evidence_issues=_int(env.get("MAX_EVIDENCE_ISSUES"), base.max_evidence_issues, 1, 20),
            max_post_chars=_int(env.get("MAX_POST_CHARS"), base.max_post_chars, 500, 3500),
        )


def _int(raw: str | None, default: int, low: int, high: int) -> int:
    if not raw:
        return default
    try:
        return max(low, min(high, int(raw)))
    except ValueError:
        log.warning("ignoring unreadable setting %r; using %s", raw, default)
        return default


CONFIG = Config.from_env()
