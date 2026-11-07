"""Settings. Every one has a built-in default; the environment only overrides it, and an unreadable
override falls back to the default (logged), so the agent never needs env config to run."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

log = logging.getLogger("spec_breaker.config")


@dataclass(frozen=True)
class Config:
    max_spec_chars: int = 60_000      # read from the document or the paste; the rest is stated, not used
    chunk_chars: int = 6_000          # target chunk size for the split calls
    max_chunks: int = 8               # split calls per turn
    max_issues: int = 15              # proposed issues kept; the rest are "ask for the rest"
    max_teams_offered: int = 10       # options on the team question
    max_edit_rounds: int = 3
    duplicate_overlap: float = 0.8    # share of title tokens that marks an existing issue as the same
    turn_deadline_secs: float = 300.0
    turn_llm_calls: int = 24          # 1 scope + 4 doc + 4 team + 8 split + 5 dup + 2 spare
    max_instruction_chars: int = 2_000

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        base = Config()
        return Config(
            max_spec_chars=_int(env.get("SPEC_MAX_CHARS"), base.max_spec_chars, 2_000, 100_000),
            max_issues=_int(env.get("SPEC_MAX_ISSUES"), base.max_issues, 1, 15),
            turn_deadline_secs=float(_int(env.get("SPEC_TURN_DEADLINE_SECS"), int(base.turn_deadline_secs), 60, 540)),
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
