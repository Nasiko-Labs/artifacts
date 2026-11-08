"""Settings. Every one has a built-in default; the environment only overrides it, and an unreadable
override falls back to the default (logged), so the agent never needs env config to run."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

log = logging.getLogger("escalation.config")

ESCALATION_PHRASES = ("escalate", "manager", "supervisor", "cancel", "legal", "lawyer", "refund", "chargeback",
                      "unacceptable", "third time", "still not", "again")
OPEN_STATUSES = ("new", "open", "pending", "hold", "on-hold", "on hold")


@dataclass(frozen=True)
class Config:
    window_days: int = 30
    min_window_days: int = 7
    max_window_days: int = 90
    top_n: int = 10
    max_top_n: int = 25
    max_tickets: int = 60          # the most recently updated tickets read per turn
    max_comments: int = 12         # last comments kept per ticket
    sentiment_batch: int = 10      # tickets per sentiment call
    customer_msgs_for_sentiment: int = 3
    bug_tickets: int = 10          # at-risk tickets whose bugs are looked up
    max_bugs_per_ticket: int = 2
    ticket_tool_calls: int = 70
    bug_tool_calls: int = 10
    max_drafts: int = 3            # status replies drafted per turn
    max_reply_chars: int = 900
    turn_deadline_secs: float = 300.0
    turn_llm_calls: int = 24
    escalation_phrases: tuple[str, ...] = ESCALATION_PHRASES

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        base = Config()
        return Config(
            window_days=_int(env.get("WINDOW_DAYS"), base.window_days, base.min_window_days, base.max_window_days),
            top_n=_int(env.get("TOP_N"), base.top_n, 1, base.max_top_n),
            max_tickets=_int(env.get("MAX_TICKETS"), base.max_tickets, 1, 60),
            escalation_phrases=_words(env.get("ESCALATION_PHRASES"), base.escalation_phrases),
        )


def _words(raw: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not raw:
        return default
    words = tuple(w.strip().casefold() for w in raw.split(",") if w.strip())
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
