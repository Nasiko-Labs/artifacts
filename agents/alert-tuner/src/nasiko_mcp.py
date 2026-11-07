"""Async client for the Nasiko MCP gateway (copied in from the MCP kit; owned by this agent).

The platform injects `MCP_GATEWAY_URL` and `MCP_GATEWAY_TOKEN` at deploy. The agent sends
JSON-RPC `tools/list` / `tools/call` with `Authorization: Bearer <token>` and the inbound
W3C `traceparent` (without it `tools/call` is a 403: docs/MCP_GATEWAY_AGENT_AUTH.md).

Tool names are passed through exactly as listed (`{connector-prefix}__{tool}` for uploaded and
remote connectors, bare for hosted-connector and system tools). Nothing here renames or strips them.

Gateway outcomes (nasiko-cloud-rs oss/mcp-gateway/src/types.rs::codes, protocol.rs):
  result                -> ToolOk
  -32001 TOOL_ASK       -> ApprovalPending  (does not block: pause the A2A task, then re-call)
  -32000 TOOL_BLOCKED   -> ToolDenied       (by_user when the human rejected an approval)
  -32002 AUTH_REQUIRED  -> AuthorizationRequired (connector needs the user to connect/OAuth)
  -32602 "Unknown tool" -> UnknownToolError
  HTTP 401 / 403        -> GatewayAuthError / GatewayForbiddenError
  timeout, network, 5xx -> GatewayUnavailableError
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import httpx

from nasiko_trace import suppressed_http_instrumentation

log = logging.getLogger("nasiko_mcp")

ENV_URL = "MCP_GATEWAY_URL"
ENV_TOKEN = "MCP_GATEWAY_TOKEN"
ENV_TIMEOUT = "MCP_GATEWAY_TIMEOUT_SECS"
DEFAULT_TIMEOUT_SECS = 120.0

TOOL_BLOCKED = -32000
TOOL_ASK = -32001
AUTH_REQUIRED = -32002
INVALID_PARAMS = -32602
_DENIED_BY_USER = "denied by the user"
_UNKNOWN_TOOL = "Unknown tool"
_MAX_ERROR_BODY_CHARS = 300
_RETRYABLE_HTTP = frozenset({408, 429})

# OpenAI rejects a request whose function names don't match this; such tools are skipped.
OPENAI_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# --- errors -----------------------------------------------------------------

class McpGatewayError(Exception):
    """Base class for every gateway failure that is not a typed tool result."""


class MissingTraceparentError(McpGatewayError, ValueError):
    """`tools/call` needs the inbound traceparent; refused before any network call."""


class GatewayAuthError(McpGatewayError):
    """HTTP 401: the gateway token is missing, unknown or revoked."""


class GatewayForbiddenError(McpGatewayError):
    """HTTP 403: typically the traceparent names no live flow this agent is part of."""


class UnknownToolError(McpGatewayError):
    """The gateway has no connector for this tool name."""


class GatewayUnavailableError(McpGatewayError):
    """Timeout, connection failure or 5xx."""


class GatewayProtocolError(McpGatewayError):
    """Unexpected response shape, or a JSON-RPC error code with no typed meaning."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


# --- values -----------------------------------------------------------------

@dataclass(frozen=True)
class GatewayConfig:
    url: str
    token: str = field(repr=False)
    timeout_secs: float = DEFAULT_TIMEOUT_SECS

    @staticmethod
    def from_env(env: Mapping[str, str] | None = None) -> GatewayConfig | None:
        """None when the platform injected no gateway (local runs, no grants)."""
        source = os.environ if env is None else env
        url = (source.get(ENV_URL) or "").strip()
        token = (source.get(ENV_TOKEN) or "").strip()
        if not url or not token:
            return None
        return GatewayConfig(url=url, token=token, timeout_secs=_timeout_from(source.get(ENV_TIMEOUT)))


def _timeout_from(raw: str | None) -> float:
    if raw is None or not raw.strip():
        return DEFAULT_TIMEOUT_SECS
    try:
        value = float(raw)
    except ValueError as e:
        raise ValueError(f"{ENV_TIMEOUT} must be a number, got {raw!r}") from e
    if value <= 0:
        raise ValueError(f"{ENV_TIMEOUT} must be positive, got {raw!r}")
    return value


@dataclass(frozen=True)
class McpTool:
    """One listed tool, unchanged: the full name is the routing key."""

    name: str
    description: str
    input_schema: Mapping[str, Any]

    def to_openai(self) -> dict:
        parameters = {"type": "object", "properties": {}, **dict(self.input_schema)}
        return {
            "type": "function",
            "function": {"name": self.name, "description": self.description, "parameters": parameters},
        }


@dataclass(frozen=True)
class ToolOk:
    content: tuple[Mapping[str, Any], ...]
    is_error: bool
    raw: Mapping[str, Any]

    @property
    def text(self) -> str:
        texts = [str(item.get("text", "")) for item in self.content if item.get("type") == "text"]
        return "\n".join(texts)


@dataclass(frozen=True)
class ApprovalPending:
    """The tool is under `ask`. Nothing ran. Pause the A2A task with the hitl request id,
    then call the same tool again with the same arguments once the human approved."""

    tool_name: str
    message: str
    hitl_request_ids: tuple[str, ...]
    server: str | None

    @property
    def hitl_request_id(self) -> str | None:
        return self.hitl_request_ids[0] if self.hitl_request_ids else None


@dataclass(frozen=True)
class ToolDenied:
    tool_name: str
    message: str
    by_user: bool


@dataclass(frozen=True)
class AuthorizationRequired:
    """The connector needs the user to connect or re-authorize it. Nothing ran."""

    tool_name: str
    message: str
    hitl_request_id: str | None
    connector: str | None


ToolResult = ToolOk | ApprovalPending | ToolDenied | AuthorizationRequired


def openai_tools(tools: Iterable[McpTool]) -> list[dict]:
    """OpenAI function schemas for the listed tools, names and schemas unchanged."""
    schemas = []
    for tool in tools:
        if not OPENAI_TOOL_NAME.match(tool.name):
            log.warning("tool %r is not a valid OpenAI function name; not offered", tool.name)
            continue
        schemas.append(tool.to_openai())
    return schemas


# --- client -----------------------------------------------------------------

class NasikoMcpClient:
    def __init__(self, config: GatewayConfig, http: httpx.AsyncClient | None = None):
        self._config = config
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient()

    @classmethod
    def from_env(cls, http: httpx.AsyncClient | None = None) -> NasikoMcpClient | None:
        config = GatewayConfig.from_env()
        return cls(config, http) if config else None

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def list_tools(self, traceparent: str | None) -> tuple[McpTool, ...]:
        try:
            result = await self._rpc("tools/list", None, traceparent)
        except _RpcError as e:
            raise GatewayProtocolError(f"tools/list failed: {e.message}", e.code) from e
        raw_tools = result.get("tools")
        if not isinstance(raw_tools, list):
            raise GatewayProtocolError("tools/list result has no `tools` list")
        return tuple(tool for tool in (_parse_tool(raw) for raw in raw_tools) if tool is not None)

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        if not traceparent:
            raise MissingTraceparentError("tools/call requires the inbound traceparent")
        params = {"name": name, "arguments": dict(arguments)}
        try:
            result = await self._rpc("tools/call", params, traceparent)
        except _RpcError as e:
            return _typed_tool_error(name, e)
        content = result.get("content")
        items = tuple(item for item in content if isinstance(item, Mapping)) if isinstance(content, list) else ()
        return ToolOk(content=items, is_error=bool(result.get("isError")), raw=MappingProxyType(dict(result)))

    async def _rpc(self, method: str, params: dict | None, traceparent: str | None) -> dict:
        request: dict[str, Any] = {"jsonrpc": "2.0", "id": str(uuid.uuid4()), "method": method}
        if params is not None:
            request["params"] = params
        response = await self._post(request, traceparent)
        _raise_for_http_status(response)
        return _rpc_result(response)

    async def _post(self, request: dict, traceparent: str | None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self._config.token}"}
        if traceparent:
            headers["traceparent"] = traceparent
        try:
            with suppressed_http_instrumentation():
                return await self._http.post(
                    self._config.url, json=request, headers=headers, timeout=self._config.timeout_secs
                )
        except httpx.TimeoutException as e:
            raise GatewayUnavailableError(
                f"MCP gateway timed out after {self._config.timeout_secs}s ({request['method']})"
            ) from e
        except httpx.HTTPError as e:
            raise GatewayUnavailableError(f"MCP gateway unreachable ({request['method']}): {e}") from e


class _RpcError(Exception):
    """Internal: a JSON-RPC error that `call_tool` turns into a typed ToolResult."""

    def __init__(self, code: int, message: str, data: Mapping[str, Any]):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def _raise_for_http_status(response: httpx.Response) -> None:
    status = response.status_code
    if status < 400:
        return
    body = response.text[:_MAX_ERROR_BODY_CHARS]
    if status == 401:
        raise GatewayAuthError(f"MCP gateway rejected the token (401): {body}")
    if status == 403:
        raise GatewayForbiddenError(f"MCP gateway refused the call (403): {body}")
    if status >= 500 or status in _RETRYABLE_HTTP:
        raise GatewayUnavailableError(f"MCP gateway error {status}: {body}")
    raise GatewayProtocolError(f"MCP gateway returned HTTP {status}: {body}")


def _rpc_result(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError as e:
        raise GatewayProtocolError(f"MCP gateway returned invalid JSON (HTTP {response.status_code})") from e
    if not isinstance(body, dict):
        raise GatewayProtocolError("MCP gateway response is not a JSON object")
    error = body.get("error")
    if isinstance(error, dict):
        _raise_rpc_error(error)
    result = body.get("result")
    if not isinstance(result, dict):
        raise GatewayProtocolError("MCP gateway response has neither result nor error")
    return result


def _raise_rpc_error(error: dict) -> None:
    code = error.get("code")
    message = str(error.get("message") or "unknown error")
    data = error.get("data")
    if code in (TOOL_ASK, TOOL_BLOCKED, AUTH_REQUIRED):
        raise _RpcError(code, message, MappingProxyType(data if isinstance(data, dict) else {}))
    if code == INVALID_PARAMS and message.startswith(_UNKNOWN_TOOL):
        raise UnknownToolError(message)
    raise GatewayProtocolError(f"MCP error {code}: {message}", code if isinstance(code, int) else None)


def _typed_tool_error(name: str, error: _RpcError) -> ToolResult:
    if error.code == TOOL_ASK:
        return ApprovalPending(name, error.message, _hitl_ids(error.data), _str_or_none(error.data.get("server")))
    if error.code == AUTH_REQUIRED:
        return AuthorizationRequired(
            name, error.message, _str_or_none(error.data.get("hitl_request_id")),
            _str_or_none(error.data.get("connector")),
        )
    return ToolDenied(name, error.message, by_user=_DENIED_BY_USER in error.message)


def _hitl_ids(data: Mapping[str, Any]) -> tuple[str, ...]:
    """Single-tool asks carry `hitl_request_id`; hosted-connector batches `hitl_request_ids`.
    Either is absent when the gateway could not persist the request (no verified flow)."""
    plural = data.get("hitl_request_ids")
    if isinstance(plural, list):
        return tuple(str(i) for i in plural if i)
    single = data.get("hitl_request_id")
    return (str(single),) if single else ()


def _str_or_none(value: Any) -> str | None:
    return str(value) if value else None


def _parse_tool(raw: Any) -> McpTool | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not raw["name"]:
        log.warning("skipping malformed tools/list entry")
        return None
    schema = raw.get("inputSchema")
    return McpTool(
        name=raw["name"],
        description=str(raw.get("description") or ""),
        input_schema=MappingProxyType(dict(schema) if isinstance(schema, dict) else {}),
    )
