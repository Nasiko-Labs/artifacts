"""Settings. Every one has a built-in default; the environment only overrides it, and an unreadable
override falls back to the default (logged), so the agent never needs env config to run."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

log = logging.getLogger("bug_notifier.config")

_DEFAULT_DONE = ("done", "released", "closed", "resolved")
_DEFAULT_TIERS = ("enterprise", "business", "pro", "standard")


@dataclass(frozen=True)
class Config:
    done_statuses: tuple[str, ...] = _DEFAULT_DONE   # casefolded tracker statuses that count as fixed
    tier_order: tuple[str, ...] = _DEFAULT_TIERS     # best first; anything else is "unknown"
    sweep_days: int = 7
    max_sweep_days: int = 30
    max_tickets: int = 30          # kept after the checks
    max_offered: int = 20          # options on the pick card (the platform's limit)
    max_drafts: int = 8            # drafts per turn
    keyword_lookback_days: int = 90
    turn_deadline_secs: float = 420.0
    turn_llm_calls: int = 18       # hard cap per turn (17 for a full pull + 1 follow-up routing call)
    max_instruction_chars: int = 500
    max_reply_chars: int = 1200

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        base = Config()
        return Config(
            done_statuses=_words(env.get("DONE_STATUSES"), base.done_statuses),
            tier_order=_words(env.get("TIER_ORDER"), base.tier_order),
            sweep_days=_int(env.get("SWEEP_DAYS"), base.sweep_days, 1, base.max_sweep_days),
            max_tickets=_int(env.get("MAX_TICKETS"), base.max_tickets, 1, 100),
            max_drafts=_int(env.get("MAX_DRAFTS_PER_TURN"), base.max_drafts, 1, 20),
            keyword_lookback_days=_int(env.get("KEYWORD_LOOKBACK_DAYS"), base.keyword_lookback_days, 1, 365),
        )


def _words(raw: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not raw:
        return default
    words = tuple(w.strip().casefold() for w in raw.replace(">", ",").split(",") if w.strip())
    return words or default


def _int(raw: str | None, default: int, low: int, high: int) -> int:
    if not raw:
        return default
    try:
        return max(low, min(high, int(raw)))
    except ValueError:
        log.warning("ignoring unreadable setting %r; using %s", raw, default)
        return default


CONFIG = Config.from_env()
