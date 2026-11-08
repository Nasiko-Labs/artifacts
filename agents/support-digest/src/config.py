"""Settings. Every one has a built-in default; the environment only overrides it, and an unreadable
override falls back to the default (logged), so the agent never needs env config to run."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

log = logging.getLogger("support_digest.config")

PRIORITIES = ("urgent", "high", "normal", "low")
PRIORITY_ALIASES = {"critical": "urgent", "p1": "urgent", "p2": "high", "medium": "normal", "p3": "normal",
                    "p4": "low"}
# Calendar minutes. Used only when the desk exposes no readable SLA policy, and always labelled.
FIRST_REPLY_MINUTES = {"urgent": 60, "high": 240, "normal": 480, "low": 1440}
RESOLUTION_MINUTES = {"urgent": 1440, "high": 4320, "normal": 7200, "low": 14400}
OPEN_STATUSES = ("new", "open", "pending", "hold", "on-hold", "on hold")
AWAITING_REPLY_STATUSES = ("new", "open")
TIERS = ("enterprise", "business", "premium", "pro", "growth", "team", "starter", "basic", "free")


@dataclass(frozen=True)
class Config:
    period_days: int = 7
    min_period_days: int = 1
    max_period_days: int = 31
    max_tickets: int = 300          # sample cap per run, newest first
    theme_batch: int = 25           # tickets per labelling call
    max_theme_chunks: int = 12
    max_labels: int = 120           # distinct labels the cluster call sees
    theme_min_count: int = 3
    top_n: int = 5
    current_tool_calls: int = 60
    previous_tool_calls: int = 5
    sla_tool_calls: int = 5
    max_breach_lines: int = 50      # breaches kept for follow-up questions
    turn_deadline_secs: float = 300.0
    turn_llm_calls: int = 28

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        base = Config()
        return Config(
            period_days=_int(env.get("PERIOD_DAYS"), base.period_days, base.min_period_days, base.max_period_days),
            max_tickets=_int(env.get("MAX_TICKETS"), base.max_tickets, 1, 300),
            theme_min_count=_int(env.get("THEME_MIN_COUNT"), base.theme_min_count, 1, 50),
        )


def _int(raw: str | None, default: int, low: int, high: int) -> int:
    if not raw:
        return default
    try:
        return max(low, min(high, int(raw)))
    except ValueError:
        log.warning("ignoring unreadable setting %r; using %s", raw, default)
        return default


def norm_priority(raw: str) -> str:
    """A desk priority mapped onto urgent/high/normal/low; anything else (or nothing) is `normal`."""
    word = (raw or "").strip().casefold()
    word = PRIORITY_ALIASES.get(word, word)
    return word if word in PRIORITIES else "normal"


CONFIG = Config.from_env()
