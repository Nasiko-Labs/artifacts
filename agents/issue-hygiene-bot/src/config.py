"""Settings. Every one has a built-in default; the environment only overrides it, and an unreadable
override falls back to the default (logged), so the agent never needs env config to run."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

log = logging.getLogger("issue_hygiene.config")


@dataclass(frozen=True)
class Config:
    stale_days: int = 14              # H3: started and not updated for at least this many days
    old_days: int = 90                # H4: open and created at least this many days ago
    estimate_adoption_pct: int = 30   # H2 runs for a team only when this share of its open issues has an estimate
    max_issues: int = 200             # kept after the checks (stated when hit)
    max_teams: int = 3                # teams read when no team is named
    max_owners: int = 10              # owners looked up in chat and offered on the picker
    max_dm_issues: int = 8            # issues listed in one message; the rest are counted
    max_custom_chars: int = 300       # the picker's typed line
    max_message_chars: int = 3500     # an edited message longer than this is refused
    turn_deadline_secs: float = 300.0
    turn_llm_calls: int = 26          # hard cap per turn (issues 7 + owners 4 + intros 10 + 5 spare)

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        base = Config()
        return Config(
            stale_days=_int(env.get("HYGIENE_STALE_DAYS"), base.stale_days, 1, 365),
            old_days=_int(env.get("HYGIENE_OLD_DAYS"), base.old_days, 1, 3650),
            estimate_adoption_pct=_int(env.get("HYGIENE_ESTIMATE_ADOPTION_PCT"), base.estimate_adoption_pct, 0, 100),
            max_issues=_int(env.get("HYGIENE_MAX_ISSUES"), base.max_issues, 1, 500),
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
