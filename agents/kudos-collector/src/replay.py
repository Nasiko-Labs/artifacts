"""Argument-template replay: saves an LLM call per person.

Once the model has made one successful call whose arguments name exactly one person (a value
equal to one of their names or handles, or containing it as a whole word), code repeats the same tool
with the same arguments for every other member, substituting only that value with the other member's
value of the same kind. The extra results are appended to the original result, so the model
transcribes everyone in one pass. No match, or a match on more than one person: no replay.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from nasiko_mcp import ToolOk, ToolResult

log = logging.getLogger("kudos_collector.replay")

Known = Mapping[str, str]  # kind ("handle", "name", "email", "label") -> value as the tools wrote it


class ReplayCaller:
    """A ToolCaller that replays the first person-specific successful call once per step."""

    def __init__(self, inner: Any, people: Sequence[tuple[str, Known]]):
        self._inner = inner
        self._people = tuple(people)
        self._done = False

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._inner.call_tool(name, arguments, traceparent)
        if self._done or len(self._people) < 2 or not isinstance(result, ToolOk) or result.is_error:
            return result
        match = match_person(arguments, self._people)
        if match is None:
            return result
        self._done = True
        label, kind, value = match
        variants = [(other, substitute(arguments, value, known[kind])) for other, known in self._people
                    if other != label and known.get(kind)]
        log.info("replaying %s for %d more people", name, len(variants))
        outcomes = await asyncio.gather(*(self._inner.call_tool(name, args, traceparent) for _, args in variants),
                                        return_exceptions=True)
        extra = [{"type": "text", "text": f"Results of the same call for {other} (repeated by the agent):\n"
                                          f"{_text(outcome)}"}
                 for (other, _), outcome in zip(variants, outcomes, strict=True)]
        return ToolOk(content=(*result.content, *extra), is_error=False, raw=result.raw)


def match_person(arguments: Mapping[str, Any], people: Sequence[tuple[str, Known]]) -> tuple[str, str, str] | None:
    """(label, kind, value) when the arguments name exactly one person, else None."""
    leaves = list(_leaves(arguments))
    hits = {(label, kind, value) for label, known in people for kind, value in known.items()
            if value and any(_contains(leaf, value) for leaf in leaves)}
    labels = {h[0] for h in hits}
    if len(labels) != 1:
        return None
    return max(sorted(hits), key=lambda h: len(h[2]))  # the longest matching value of that person


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
