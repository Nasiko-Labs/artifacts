"""Stop the model retrying tools that cannot work for this user.

Wraps the turn's gateway. A tool whose error says the capability is unavailable (not enabled, missing
scope, wrong token type, forbidden ...) is dropped for the rest of the turn, with the reason kept for
the reply. A call identical (same tool, same arguments) to one that already failed is answered with a
short note instead of reaching the gateway. One instance per turn; never persisted.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from nasiko_mcp import McpGatewayError, ToolDenied, ToolOk, ToolResult

log = logging.getLogger("kudos_collector.tool_health")

# Case-insensitive substrings that, inside an error result, mean the capability is not usable.
UNAVAILABLE_ERRORS: tuple[str, ...] = (
    "feature_not_enabled",
    "missing_scope",
    "not_allowed_token_type",
    "not_authed",
    "invalid_auth",
    "access_denied",
    "insufficient permission",
    "insufficient_permission",
    "permission_denied",
    "forbidden",
    "403",
)
_FAILED_FLAGS = ("ok", "successful", "success")
_MAX_REASON_CHARS = 60


class ToolHealth:
    """A ToolCaller that remembers failures for one turn."""

    def __init__(self, gateway: Any):
        self._gateway = gateway
        self._failed: frozenset[str] = frozenset()
        self._dropped: Mapping[str, str] = {}

    @property
    def dropped(self) -> Mapping[str, str]:
        """Tool name → the unavailable marker its error carried."""
        return dict(self._dropped)

    def is_offered(self, name: str) -> bool:
        return name not in self._dropped

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        if name in self._dropped:
            return _note(f"{name} is unavailable for this user ({self._dropped[name]}). Do not call it again; "
                         "use a different tool or stop.")
        key = _key(name, arguments)
        if key in self._failed:
            log.info("refused an identical repeat of a failed call to %s", name)
            return _note("This exact call already failed. Do not repeat it; try a different tool or stop.")
        try:
            result = await self._gateway.call_tool(name, arguments, traceparent)
        except McpGatewayError as e:  # still raised: the loop reports it (or ends the turn if fatal)
            self._record_failure(name, key, str(e))
            raise
        text = failure_text(result)
        if text is not None:
            self._record_failure(name, key, text)
        return result

    def _record_failure(self, name: str, key: str, text: str) -> None:
        self._failed = self._failed | {key}
        reason = unavailable_reason(text)
        if reason is not None:
            log.warning("dropping %s for this turn: %s", name, reason)
            self._dropped = {**self._dropped, name: reason}


def failure_text(result: ToolResult) -> str | None:
    """The result's text when it is an error (flagged, denied, or an error-shaped JSON body), else None."""
    if isinstance(result, ToolDenied):
        return result.message
    if not isinstance(result, ToolOk):
        return None  # a pause, handled by the loop
    text = _original_text(result)
    if result.is_error or _is_error_body(text):
        return text
    return None


def unavailable_reason(text: str) -> str | None:
    lowered = text.casefold()
    return next((marker[:_MAX_REASON_CHARS] for marker in UNAVAILABLE_ERRORS if marker in lowered), None)


def _original_text(result: ToolOk) -> str:
    """The gateway's own text (the fence adapter rewrites `content` but keeps `raw`)."""
    content = result.raw.get("content") if isinstance(result.raw, Mapping) else None
    if isinstance(content, list):
        texts = [str(i.get("text", "")) for i in content if isinstance(i, Mapping) and i.get("type") == "text"]
        if texts:
            return "\n".join(texts)
    return result.text


def _is_error_body(text: str) -> bool:
    try:
        body = json.loads(text)
    except ValueError:
        return False
    return any(_flags_failure(part) for part in (body, body.get("data") if isinstance(body, dict) else None))


def _flags_failure(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    return any(body.get(flag) is False for flag in _FAILED_FLAGS) or bool(body.get("error"))


def _key(name: str, arguments: Mapping[str, Any]) -> str:
    return f"{name}\n{json.dumps(arguments, sort_keys=True, default=str)}"


def _note(message: str) -> ToolOk:
    return ToolOk(content=({"type": "text", "text": json.dumps({"error": message})},), is_error=True, raw={})
