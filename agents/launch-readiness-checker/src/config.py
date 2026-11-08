"""Every threshold and budget, in one place. Each has a default; an env var only overrides it."""

from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value > 0 else default


# Budgets (plan: <= 20 LLM calls, <= 60 tool calls, 300 s per turn).
MAX_LLM_CALLS = _int("LAUNCH_MAX_LLM_CALLS", 20)
STEP_LOOP_CALLS = 4            # LLM calls per step's tool loop; plus one JSON repair
STEP_TOOL_CALLS = 15           # tool calls per step (4 steps -> 60)
STEP_DEADLINE_SECS = _int("LAUNCH_STEP_DEADLINE_SECS", 240)
TURN_DEADLINE_SECS = 300

# Caps on what a step keeps.
MAX_ISSUES = 200
MAX_TASKS = 100
MAX_DOCS = 20
MAX_MACROS = 50
MAX_ARTICLES = 50
DOC_TEXT_CHARS = 12_000
PASTE_LIMIT_CHARS = 100_000

# Rules.
DOC_FRESH_DAYS = _int("LAUNCH_DOC_FRESH_DAYS", 14)
LAUNCH_SOON_DAYS = 3
BODY_MENTION_SHARE = 0.6
MIN_TOKEN_CHARS = 3
DROPPED_SHARE_LOW = 0.2        # dropped items above this share -> confidence low

# Rule strictness (plan risk: SUP-1 and ENG-5 may be too harsh for some teams).
SUP1_BLOCKING = os.environ.get("LAUNCH_SUP1_BLOCKING", "true").casefold() != "false"
ENG5_BLOCKING = os.environ.get("LAUNCH_ENG5_BLOCKING", "true").casefold() != "false"
