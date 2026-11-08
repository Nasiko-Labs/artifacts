"""Argument-template replay for the history step: saves one LLM call per issue.

Once the model has made one successful call whose arguments name exactly one sprint issue key (a value
equal to the key, or containing it as a whole word), code repeats the same tool with the same arguments
for every other key, substituting only that value. The extra results are appended to the original
result, so the model transcribes every issue's history in one pass. No match, or a match on more than
one key: no replay. Adapted from the team-pulse pattern; owned here.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from nasiko_mcp import ToolOk, ToolResult

log = logging.getLogger("scope_creep.replay")

REPLAY_CONCURRENCY = 8
MAX_EXTRA_CHARS = 4_000  # per replayed issue, so one long history cannot crowd out the others


class ReplayCaller:
    """A ToolCaller that replays the first single-key successful call once per step."""

    def __init__(self, inner: Any, keys: Sequence[str]):
        self._inner = inner
        self._keys = tuple(dict.fromkeys(k for k in keys if k))
        self._done = False

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._inner.call_tool(name, arguments, traceparent)
        if self._done or len(self._keys) < 2 or not isinstance(result, ToolOk) or result.is_error:
            return result
        key = match_key(arguments, self._keys)
        if key is None:
            return result
        self._done = True
        others = [k for k in self._keys if k != key]
        log.info("replaying %s for %d more issues", name, len(others))
        gate = asyncio.Semaphore(REPLAY_CONCURRENCY)

        async def one(other: str) -> Any:
            async with gate:
                return await self._inner.call_tool(name, substitute(arguments, key, other), traceparent)

        outcomes = await asyncio.gather(*(one(k) for k in others), return_exceptions=True)
        extra = [{"type": "text", "text": f"Results of the same call for {other} (repeated by the agent):\n"
                                          f"{_text(outcome)[:MAX_EXTRA_CHARS]}"}
                 for other, outcome in zip(others, outcomes, strict=True)]
        return ToolOk(content=(*result.content, *extra), is_error=False, raw=result.raw)


def match_key(arguments: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    """The one key the arguments name verbatim, else None."""
    leaves = list(_leaves(arguments))
    hits = {k for k in keys if any(_contains(leaf, k) for leaf in leaves)}
    return next(iter(hits)) if len(hits) == 1 else None


def substitute(arguments: Any, old: str, new: str) -> Any:
    """A copy of the arguments with every occurrence of `old` (whole value or whole word) replaced."""
    if isinstance(arguments, Mapping):
        return {k: substitute(v, old, new) for k, v in arguments.items()}
    if isinstance(arguments, list):
        return [substitute(v, old, new) for v in arguments]
    if isinstance(arguments, str):
        return new if arguments == old else _word(old).sub(lambda _: new, arguments)
    return arguments


def _leaves(value: Any):
    if isinstance(value, Mapping):
        for v in value.values():
            yield from _leaves(v)
    elif isinstance(value, list):
        for v in value:
            yield from _leaves(v)
    elif isinstance(value, str):
        yield value


def _contains(leaf: str, value: str) -> bool:
    return leaf == value or bool(_word(value).search(leaf))


def _word(value: str) -> re.Pattern:
    return re.compile(rf"(?<![\w@.-]){re.escape(value)}(?![\w@-])")


def _text(outcome: Any) -> str:
    if isinstance(outcome, BaseException):
        return json.dumps({"error": type(outcome).__name__})
    if isinstance(outcome, ToolOk):
        return outcome.text
    return json.dumps({"error": getattr(outcome, "message", "not available")})
