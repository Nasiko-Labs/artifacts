"""The listing fallback: when no listing tool can be called from code (its arguments are not plain
owner/repository fields, e.g. a search query), a short tool loop lets the model call it. Raw results
are kept for code to read; the model sees only a fenced, capped copy and never a write tool."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import callers
import prompts
from deps import TurnDeps
from nasiko_mcp import McpTool, ToolOk, ToolResult
from tool_loop import run_tool_loop

log = logging.getLogger("pr_review_queue.list_step")

LIST_LOOP_CALLS = 2
MAX_FENCED_CHARS = 12_000   # below the loop's 16k truncation, so the closing fence reaches the model


class RecordingGateway:
    """Forwards each call unchanged (same name, arguments, traceparent); keeps the raw text of every
    successful result and hands the model a fenced copy."""

    def __init__(self, inner: Any):
        self._inner = inner
        self.texts: tuple[str, ...] = ()

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        result = await self._inner.call_tool(name, arguments, traceparent)
        if not isinstance(result, ToolOk):
            return result
        raw = result.text or json.dumps(dict(result.raw), default=str)
        if not result.is_error:
            self.texts = (*self.texts, raw)
        body = raw if len(raw) <= MAX_FENCED_CHARS else raw[:MAX_FENCED_CHARS] + "\n[truncated]"
        return ToolOk(content=({"type": "text", "text": prompts.fence(body)},), is_error=result.is_error,
                      raw=result.raw)


async def run(deps: TurnDeps, health: Any, tools: Sequence[McpTool], repo: str) -> list[str]:
    offered = tuple(t for t in tools if callers.fits(t, "list_prs"))
    if not offered:
        return []
    recorder = RecordingGateway(health)
    messages = ({"role": "system", "content": prompts.list_system(repo, deps.now)},
                {"role": "user", "content": f"List the open pull requests of {repo}."})
    result = await run_tool_loop(deps.llm, deps.model, messages, offered, recorder, deps.traceparent,
                                 max_iterations=LIST_LOOP_CALLS, is_offered=health.is_offered)
    log.info("listing loop for %s ended %s with %d results", repo, result.status, len(recorder.texts))
    return list(recorder.texts)
