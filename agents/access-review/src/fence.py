"""A gateway adapter that records every raw tool result for the code and hands the model only a fenced
receipt (adapted from the kit; owned here). People never reach the model: a member, collaborator or
profile listing becomes "N records fetched", and a repository listing becomes its repository names,
which the model needs to ask for each repository's collaborators. So neither personal data nor
instructions planted in a profile can reach or steer the tool loop.

One `Ledger` per turn holds every call (step, tool, arguments, raw text); each step gets its own
`RecordingGateway`, so code knows which step a result belongs to.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import records
from nasiko_mcp import ToolOk, ToolResult
from prompts import fence
from records import Call
from tool_health import failure_text

MAX_LISTED_REPOS = 60
MAX_ERROR_CHARS = 300


class Ledger:
    def __init__(self) -> None:
        self._calls: tuple[Call, ...] = ()

    def add(self, call: Call) -> None:
        self._calls = (*self._calls, call)

    @property
    def calls(self) -> tuple[Call, ...]:
        return self._calls

    def of(self, *steps: str) -> tuple[Call, ...]:
        return tuple(c for c in self._calls if c.step in steps)


def model_view(text: str, failed: bool) -> str:
    """What the model sees of one result: never a person, only a count or repository names (an error's
    own text, capped, so the model can tell a missing scope from a wrong argument)."""
    if failed:
        return "[the call failed] " + text[:MAX_ERROR_CHARS]
    repos = records.repo_names(text)
    if repos:
        shown = repos[:MAX_LISTED_REPOS]
        more = f" (+{len(repos) - len(shown)} more)" if len(repos) > len(shown) else ""
        return "[repositories: " + ", ".join(shown) + more + "]"
    count = sum(1 for o in records.objects(text) if records.s(o.get("login")) or records.s(o.get("slug")))
    return f"[{count} records fetched, read by the agent's code]"


class RecordingGateway:
    """Forwards `call_tool` unchanged (same full name, arguments, traceparent); records the raw result
    in the ledger and returns a fenced receipt. Pauses pass through unrecorded."""

    def __init__(self, inner: Any, ledger: Ledger, step: str):
        self._inner = inner
        self._ledger = ledger
        self._step = step

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._inner.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        raw = result.text or json.dumps(dict(result.raw), default=str)
        failed = failure_text(result) is not None
        if not failed and result.raw:
            self._ledger.add(Call(self._step, name, dict(arguments), raw))
        fenced = ({"type": "text", "text": fence(model_view(raw, failed))},)
        return ToolOk(content=fenced, is_error=result.is_error, raw=result.raw)
