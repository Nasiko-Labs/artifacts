"""The only write this agent makes is one confirmed comment, and this adapter holds it to that.

It judges a call by its content, not by the tool's name: the model picks the tool, but
- any long text in the arguments (at any depth, so a tool run through a catalog executor is
  covered too) must be exactly the confirmed comment body; a reworded or different body is refused;
- the body is posted at most once: after one successful call carrying it, another is refused.
Short arguments (owner, repo, number, a catalog search query) pass through unchanged.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from nasiko_mcp import NasikoMcpClient, ToolOk, ToolResult

LONG_TEXT_CHARS = 200
REWORDED = ("refused: the comment body must be exactly the confirmed text between the body markers, "
            "unchanged. Pass it verbatim.")
ALREADY_POSTED = "refused: the comment was already posted once. Do not post it again; reply that it is done."


def _normalise(text: str) -> str:
    """Line breaks are kept (markdown tables need them); only trailing spaces and CRs are ignored."""
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip().splitlines())


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def _refusal(message: str) -> ToolOk:
    return ToolOk(content=({"type": "text", "text": message},), is_error=True, raw={})


class PostGuard:
    def __init__(self, gateway: NasikoMcpClient, body: str):
        self._gateway = gateway
        self._body = _normalise(body)
        self._outcome: ToolResult | None = None

    @property
    def post_outcome(self) -> ToolResult | None:
        """The result of the last call that carried the body (None: the model never sent it)."""
        return self._outcome

    @property
    def posted(self) -> bool:
        return isinstance(self._outcome, ToolOk) and not self._outcome.is_error

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        texts = [_normalise(s) for s in _strings(arguments)]
        long_texts = [t for t in texts if len(t) > LONG_TEXT_CHARS or t == self._body]
        if any(text != self._body for text in long_texts):
            return _refusal(REWORDED)
        if not long_texts:
            return await self._gateway.call_tool(name, arguments, traceparent)
        if self.posted:
            return _refusal(ALREADY_POSTED)
        outcome = await self._gateway.call_tool(name, arguments, traceparent)
        self._outcome = outcome
        return outcome
