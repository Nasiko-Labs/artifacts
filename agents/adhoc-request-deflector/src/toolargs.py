"""Pure: arguments for a read or post tool, built from its own input schema. Code (not the model) makes
every call, so their count, targets and arguments are exact and bounded. A tool whose required
arguments cannot be mapped is not used (and the reply says which source was not read)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from nasiko_mcp import McpTool

QUERY_KEYS = ("query", "q", "search_query", "search", "title", "name", "keywords", "file_name", "search_term")
COUNT_KEYS = ("count", "limit", "per_page", "max_results", "page_size")
SHEET_ID_KEYS = ("spreadsheet_id", "spreadsheetid", "sheet_id", "file_id", "fileid", "id", "document_id")
RANGE_KEYS = ("ranges", "range", "sheet_range", "a1_range", "a1_notation", "cell_range")
CHANNEL_KEYS = ("channel", "channel_id", "conversation", "conversation_id", "channel_name")
THREAD_KEYS = ("thread_ts", "thread", "thread_id", "parent_ts", "thread_timestamp")
TEXT_KEYS = ("text", "markdown_text", "message", "body", "content")
OLDEST_KEYS = ("oldest", "after", "since", "start_time", "from")
DEFAULT_RANGE = "A1:Z300"
SEARCH_COUNT = 20
HISTORY_LIMIT = 100
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


def _is_array(tool: McpTool, key: str) -> bool:
    spec = _props(tool).get(key)
    return isinstance(spec, Mapping) and spec.get("type") == "array"


def _complete(tool: McpTool, args: dict[str, Any]) -> dict[str, Any] | None:
    """The arguments, or None when a required one is left unset."""
    return args if _required(tool) <= set(args) else None


def _with_count(tool: McpTool, args: dict[str, Any], count: int) -> dict[str, Any]:
    key = _pick(tool, COUNT_KEYS)
    return {**args, key: count} if key else args


def sheet_search_args(tool: McpTool, title: str) -> dict[str, Any] | None:
    key = _pick(tool, QUERY_KEYS)
    if key is None:
        return None
    return _complete(tool, _with_count(tool, {key: title}, SEARCH_COUNT))


def sheet_read_args(tool: McpTool, sheet_id: str) -> dict[str, Any] | None:
    id_key, range_key = _pick(tool, SHEET_ID_KEYS), _pick(tool, RANGE_KEYS)
    if id_key is None:
        return None
    args: dict[str, Any] = {id_key: sheet_id}
    if range_key:
        args[range_key] = [DEFAULT_RANGE] if _is_array(tool, range_key) else DEFAULT_RANGE
    return _complete(tool, args)


def history_args(tool: McpTool, channel_id: str, oldest: str) -> dict[str, Any] | None:
    channel_key = _pick(tool, CHANNEL_KEYS)
    if channel_key is None:
        return None
    args: dict[str, Any] = {channel_key: channel_id}
    oldest_key = _pick(tool, OLDEST_KEYS)
    if oldest_key:
        args[oldest_key] = oldest
    return _complete(tool, _with_count(tool, args, HISTORY_LIMIT))


def channels_args(tool: McpTool) -> dict[str, Any] | None:
    return _complete(tool, _with_count(tool, {}, CHANNELS_LIMIT))


def post_args(tool: McpTool, channel_id: str, thread_ts: str, text: str) -> dict[str, Any] | None:
    """A thread reply: channel, thread and text. None when the tool cannot reply in a thread."""
    channel_key, thread_key, text_key = _pick(tool, CHANNEL_KEYS), _pick(tool, THREAD_KEYS), _pick(tool, TEXT_KEYS)
    if channel_key is None or thread_key is None or text_key is None or not thread_ts:
        return None
    return _complete(tool, {channel_key: channel_id, thread_key: thread_ts, text_key: text})
