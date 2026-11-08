"""Settings, every one with a built-in default: nothing is required. The environment only overrides.

    REVIEW_MINER_MAX_REVIEWS   reviews analysed per turn (default 400, clamped 20-400)
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

MAX_REVIEWS = 400
BATCH_SIZE = 20
MAX_LABELS = 150
EXTRACT_CONCURRENCY = 4
DEADLINE_SECS = 300.0
MAX_LLM_CALLS = 25
MIN_VERSION_REVIEWS = 5
REGRESSION_MIN_RATED = 10
REGRESSION_LOW_POINTS = 15.0
REGRESSION_AVG_DROP = 0.5
NEW_THEME_MIN = 3
THEME_MIN = 3
SMALL_SAMPLE = 20
MAX_QUOTES = 3
PASTE_LIMIT = 100_000
FETCH_MAX_ITERATIONS = 4
FETCH_MAX_TOOL_CALLS = 15
MIN_REVIEWS_FOR_HIGH = 30
MAX_QUOTE_CHARS = 300


@dataclass(frozen=True)
class Config:
    max_reviews: int = MAX_REVIEWS
    batch_size: int = BATCH_SIZE
    max_labels: int = MAX_LABELS
    extract_concurrency: int = EXTRACT_CONCURRENCY
    deadline_secs: float = DEADLINE_SECS
    max_llm_calls: int = MAX_LLM_CALLS
    min_version_reviews: int = MIN_VERSION_REVIEWS
    regression_min_rated: int = REGRESSION_MIN_RATED
    regression_low_points: float = REGRESSION_LOW_POINTS
    regression_avg_drop: float = REGRESSION_AVG_DROP
    new_theme_min: int = NEW_THEME_MIN
    theme_min: int = THEME_MIN
    small_sample: int = SMALL_SAMPLE
    max_quotes: int = MAX_QUOTES
    paste_limit: int = PASTE_LIMIT
    fetch_max_iterations: int = FETCH_MAX_ITERATIONS
    fetch_max_tool_calls: int = FETCH_MAX_TOOL_CALLS
    min_reviews_for_high: int = MIN_REVIEWS_FOR_HIGH
    max_quote_chars: int = MAX_QUOTE_CHARS

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> Config:
        source = os.environ if env is None else env
        return Config(max_reviews=_int(source.get("REVIEW_MINER_MAX_REVIEWS"), MAX_REVIEWS, 20, MAX_REVIEWS))


def _int(raw: str | None, default: int, low: int, high: int) -> int:
    try:
        return min(high, max(low, int(raw))) if raw else default
    except ValueError:
        return default
