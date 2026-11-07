"""Pure: arguments for a chat read tool, built from its own input schema. Code (not the model) makes the
search and thread calls, so their count, targets and arguments are exact and bounded. A tool whose
required arguments cannot be mapped is not used (and the reply says which source was not read)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nasiko_mcp import McpTool

QUERY_KEYS = ("query", "q", "search_query", "search", "text", "keywords", "term")
COUNT_KEYS = ("count", "limit", "per_page", "max_results", "page_size")
CHANNEL_KEYS = ("channel", "channel_id", "conversation", "conversation_id", "channel_name")
TS_KEYS = ("ts", "thread_ts", "message_ts", "parent_ts", "thread_id")
OLDEST_KEYS = ("oldest", "after", "since", "start_time", "from")
SEARCH_COUNT = 20
REPLIES_LIMIT = 100
HISTORY_LIMIT = 200
CHANNELS_LIMIT = 200


def _props(tool: McpTool) -> Mapping[str, Any]:
    props = tool.input_schema.get("properties")
    return props if isinstance(props, Mapping) else {}


def _required(tool: McpTool) -> frozenset[str]:
    required = tool.input_schema.get("required")
    return frozenset(r for r in required if isinstance(r, str)) if isinstance(required, list) else frozenset()


def _pick(tool: McpTool, keys: tuple[str, ...]) -> str | None:
    props = {p.casefold(): p for p in _props(tool)}
    return next((props[k] for k in keys if k in props), None)


def _complete(tool: McpTool, args: dict[str, Any]) -> dict[str, Any] | None:
    """The arguments, or None when a required one is left unset."""
    return args if _required(tool) <= set(args) else None


def search_args(tool: McpTool, query: str, channel: str | None = None) -> dict[str, Any] | None:
    key = _pick(tool, QUERY_KEYS)
    if key is None:
        return None
    args: dict[str, Any] = {}
    channel_key = _pick(tool, CHANNEL_KEYS)
    if channel and channel_key:
        args[channel_key] = channel
    elif channel:
        query = f"{query} in:#{channel}"
    args[key] = query
    count_key = _pick(tool, COUNT_KEYS)
    if count_key:
        args[count_key] = SEARCH_COUNT
    return _complete(tool, args)


def replies_args(tool: McpTool, channel_id: str, ts: str) -> dict[str, Any] | None:
    channel_key, ts_key = _pick(tool, CHANNEL_KEYS), _pick(tool, TS_KEYS)
    if channel_key is None or ts_key is None:
        return None
    args: dict[str, Any] = {channel_key: channel_id, ts_key: ts}
    limit_key = _pick(tool, COUNT_KEYS)
    if limit_key:
        args[limit_key] = REPLIES_LIMIT
    return _complete(tool, args)


def history_args(tool: McpTool, channel_id: str, oldest: str) -> dict[str, Any] | None:
    channel_key = _pick(tool, CHANNEL_KEYS)
    if channel_key is None:
        return None
    args: dict[str, Any] = {channel_key: channel_id}
    for keys, value in ((COUNT_KEYS, HISTORY_LIMIT), (OLDEST_KEYS, oldest)):
        key = _pick(tool, keys)
        if key:
            args[key] = value
    return _complete(tool, args)


def channels_args(tool: McpTool) -> dict[str, Any] | None:
    limit_key = _pick(tool, COUNT_KEYS)
    return _complete(tool, {limit_key: CHANNELS_LIMIT} if limit_key else {})
