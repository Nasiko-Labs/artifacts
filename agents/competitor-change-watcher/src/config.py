"""Settings, every one with a built-in default: nothing is required. The environment only overrides.

    CHANGE_WATCHER_RECENT_DAYS   with no baseline, entries dated within this many days count as new (default 30)
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

PASTE_LIMIT = 100_000
RECENT_DAYS = 30
CLASSIFY_BATCH = 15
MAX_ENTRIES = 120
CLASSIFY_CONCURRENCY = 4
ROADMAP_TOP = 10
ROADMAP_MAX_ITERATIONS = 5
ROADMAP_MAX_TOOL_CALLS = 12
FETCH_MAX_ITERATIONS = 3
FETCH_MAX_TOOL_CALLS = 3
MAP_MIN_SCORE = 0.34
MAP_MIN_SHARED = 2
MAP_MAX_PER_CHANGE = 2
DEADLINE_SECS = 240.0
MAX_LLM_CALLS = 16
MAX_QUOTE_CHARS = 300


@dataclass(frozen=True)
class Config:
    paste_limit: int = PASTE_LIMIT
    recent_days: int = RECENT_DAYS
    classify_batch: int = CLASSIFY_BATCH
    max_entries: int = MAX_ENTRIES
    classify_concurrency: int = CLASSIFY_CONCURRENCY
    roadmap_top: int = ROADMAP_TOP
    roadmap_max_iterations: int = ROADMAP_MAX_ITERATIONS
    roadmap_max_tool_calls: int = ROADMAP_MAX_TOOL_CALLS
    fetch_max_iterations: int = FETCH_MAX_ITERATIONS
    fetch_max_tool_calls: int = FETCH_MAX_TOOL_CALLS
    map_min_score: float = MAP_MIN_SCORE
    map_min_shared: int = MAP_MIN_SHARED
    map_max_per_change: int = MAP_MAX_PER_CHANGE
    deadline_secs: float = DEADLINE_SECS
    max_llm_calls: int = MAX_LLM_CALLS
    max_quote_chars: int = MAX_QUOTE_CHARS

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> Config:
        source = os.environ if env is None else env
        return Config(recent_days=_int(source.get("CHANGE_WATCHER_RECENT_DAYS"), RECENT_DAYS, 1, 365))


def _int(raw: str | None, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(raw))) if raw else default
    except ValueError:
        return default
