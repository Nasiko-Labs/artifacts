"""Every tool the gateway lists, wrapped as a CrewAI tool. No tool or provider is named here.

The CrewAI tool's `name` is the full listed name, prefix included, and its `_run` forwards to
`tools/call` with that exact name. CrewAI rejects a function name that does not start with a
letter, and a connector prefix is hex, so such a name is offered with `t_` in front; the gateway
still receives the listed name. CrewAI sanitizes names for the model-facing schema (lowercase,
runs of `_` collapsed, so `abc__search` is offered as `abc_search`) and maps the model's call
back to this object; the gateway still receives the full name. Two listed names that sanitize to
the same string would be ambiguous to CrewAI, so only the first is offered.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from crewai.tools import BaseTool
from crewai.utilities.pydantic_schema_utils import create_model_from_schema
from crewai.utilities.string_utils import sanitize_tool_name
from pydantic import BaseModel, ConfigDict, PrivateAttr

from async_bridge import LoopBridge
from nasiko_mcp import (
    ApprovalPending,
    AuthorizationRequired,
    GatewayAuthError,
    GatewayForbiddenError,
    McpGatewayError,
    McpTool,
    MissingTraceparentError,
    NasikoMcpClient,
    ToolDenied,
    ToolOk,
    ToolResult,
)

log = logging.getLogger("category_briefing.tools")

MAX_RESULT_CHARS = 16000
# Failures the model cannot fix: the flow is broken, so the answer must not be trusted.
_FATAL = (GatewayAuthError, GatewayForbiddenError, MissingTraceparentError)


@dataclass
class ToolSession:
    """Per-request state shared by the wrapped tools (accumulators, appended from the crew's
    worker thread). `fatal` collects unrecoverable errors; `evidence` holds the text of every
    successful tool result, which the post-check uses to keep only URLs a tool returned; `stop`
    is set when the request is cancelled or out of time, so a still-running crew stops calling."""

    gateway: NasikoMcpClient
    traceparent: str
    bridge: LoopBridge
    fatal: list[Exception] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    stop: threading.Event = field(default_factory=threading.Event)


class _EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


class GatewayTool(BaseTool):
    """One listed gateway tool; `name` is what CrewAI offers, `_listed_name` what the gateway calls."""

    _session: ToolSession = PrivateAttr()
    _listed_name: str = PrivateAttr()

    def _run(self, **arguments: Any) -> str:
        session = self._session
        if session.stop.is_set():
            return "Tool call skipped: this request was cancelled or ran out of time."
        args = {key: value for key, value in arguments.items() if value is not None}
        name = self._listed_name
        session.calls.append(name)
        try:
            result = session.bridge.run(lambda: session.gateway.call_tool(name, args, session.traceparent))
        except McpGatewayError as e:
            if isinstance(e, _FATAL):
                session.fatal.append(e)
            log.warning("gateway call %s failed: %s", name, e)
            return f"Tool call failed: {e}"
        if isinstance(result, ToolOk) and not result.is_error and result.text:
            session.evidence.append(result.text)
        return _result_text(result)


def build_gateway_tools(mcp_tools: Iterable[McpTool], session: ToolSession) -> list[BaseTool]:
    tools: list[BaseTool] = []
    seen: set[str] = set()
    for mcp_tool in mcp_tools:
        crew_name = sanitize_tool_name(_offered_name(mcp_tool.name))
        if crew_name in seen:
            log.warning("tool %r collides with another tool once CrewAI sanitizes it; not offered", mcp_tool.name)
            continue
        seen.add(crew_name)
        tools.append(_wrap(mcp_tool, session))
    return tools


def _wrap(mcp_tool: McpTool, session: ToolSession) -> GatewayTool:
    tool = GatewayTool(
        name=_offered_name(mcp_tool.name),
        description=mcp_tool.description or mcp_tool.name,
        args_schema=_args_model(mcp_tool),
    )
    tool._session = session
    tool._listed_name = mcp_tool.name
    return tool


def _offered_name(listed: str) -> str:
    sanitized = sanitize_tool_name(listed)
    return listed if sanitized[:1].isalpha() else f"t_{listed}"


def _args_model(mcp_tool: McpTool) -> type[BaseModel]:
    schema = {"type": "object", "properties": {}, **dict(mcp_tool.input_schema)}
    if not schema.get("properties"):
        return _EmptyArgs
    model_name = f"{sanitize_tool_name(mcp_tool.name)}_args"
    return create_model_from_schema(schema, model_name=model_name, enrich_descriptions=True)


def _result_text(result: ToolResult) -> str:
    if isinstance(result, ToolOk):
        text = result.text or "(no text content)"
        prefix = "Tool error: " if result.is_error else ""
        return (prefix + text)[:MAX_RESULT_CHARS]
    if isinstance(result, ToolDenied):
        who = "the user" if result.by_user else "policy"
        return f"Tool call was denied by {who}: {result.message}"
    if isinstance(result, ApprovalPending | AuthorizationRequired):
        return f"Tool is not available to this read-only agent without a human step: {result.message}"
    return "Tool returned an unrecognised result."
