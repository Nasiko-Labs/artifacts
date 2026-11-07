"""Settings. Every one has a built-in default; the environment only overrides it, and an unreadable
override falls back to the default (logged), so the agent never needs env config to run."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass

log = logging.getLogger("feedback_to_roadmap.config")


@dataclass(frozen=True)
class Config:
    window_days: int = 30
    min_window_days: int = 7
    max_window_days: int = 90
    max_desk_items: int = 150
    max_chat_items: int = 100
    min_chat_chars: int = 30
    extract_chunk: int = 12
    max_extract_chunks: int = 14
    max_cluster_labels: int = 150
    max_crm_names: int = 25
    max_linked_themes: int = 8
    max_quote_themes: int = 12
    concurrency: int = 4
    turn_deadline_secs: float = 420.0
    turn_llm_calls: int = 64          # 62 for a full pull + the scope check + one follow-up routing call
    turn_tool_calls: int = 90
    desk_loop_calls: int = 4
    desk_tool_calls: int = 40
    chat_loop_calls: int = 3
    chat_tool_calls: int = 20
    crm_loop_calls: int = 4
    crm_tool_calls: int = 15
    link_loop_calls: int = 6
    link_tool_calls: int = 15
    max_paste_chars: int = 100_000

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> Config:
        env = os.environ if env is None else env
        base = Config()
        return Config(
            window_days=_int(env.get("FEEDBACK_WINDOW_DAYS"), base.window_days, 7, 90),
            max_desk_items=_int(env.get("FEEDBACK_MAX_DESK_ITEMS"), base.max_desk_items, 10, 150),
            max_chat_items=_int(env.get("FEEDBACK_MAX_CHAT_ITEMS"), base.max_chat_items, 10, 100),
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
