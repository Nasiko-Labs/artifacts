"""Make ONE approved write, built in code (no model in the write path): pick the stage's tool, fill its
arguments from the approved plan by the tool's own schema, check the call against the approval, and send
it through a gateway wrapper that lets exactly one write through per approval.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import records
import send_guard
from deps import TurnDeps
from discovery import Discoverer
from nasiko_mcp import (
    ApprovalPending,
    AuthorizationRequired,
    McpGatewayError,
    McpTool,
    ToolDenied,
    ToolOk,
    ToolResult,
)
from plan import Plan, approval
from send_guard import Approval

log = logging.getLogger("alert_tuner.sender")

_STAGE_WORDS = {"branch": "create a branch", "commit": "commit a file", "pr": "open a pull request"}


@dataclass(frozen=True)
class WriteResult:
    status: str                  # ok | error | paused | denied | blocked | not_sent
    reason: str = ""
    link: str = ""
    arguments: Mapping[str, Any] | None = None
    hitl_request_id: str = ""


class GuardedGateway:
    """Forwards one call only when the send guard allows it as THE approved write. A completed call (ok,
    error or denied) consumes the approval; a platform pause keeps it for the identical re-call."""

    def __init__(self, gateway: Any, approved: Approval):
        self._gateway = gateway
        self._approval: Approval | None = approved

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str) -> ToolResult:
        decision = send_guard.check(name, arguments, self._approval)
        if not decision.allowed or not decision.is_write:
            reason = decision.reason or "Refused: only the approved write may be made here."
            log.warning("send guard refused %s: %s", name, reason)
            return ToolOk(content=({"type": "text", "text": json.dumps({"error": reason})},), is_error=True, raw={})
        result = await self._gateway.call_tool(name, arguments, traceparent)
        if not isinstance(result, (ApprovalPending, AuthorizationRequired)):
            self._approval = None
        return result


def stage_of(tool: McpTool) -> str:
    tokens = set(send_guard.name_tokens(tool.name))
    if not send_guard.is_write_tool(tool.name) or tokens & {"delete", "remove", "merge", "close", "revert"}:
        return ""
    if "pull" in tokens and tokens & {"create", "open"}:
        return "pr"
    if tokens & {"file", "files", "contents", "content"} and tokens & {"create", "update", "put", "commit"}:
        return "commit"
    if tokens & {"branch", "ref", "reference"} and "create" in tokens:
        return "branch"
    return ""


async def pick_tools(discoverer: Discoverer) -> tuple[dict[str, str], str]:
    """Stage -> tool name from the write queries; the missing stages as words."""
    catalog = await discoverer.catalog("pr")
    tools: dict[str, str] = {}
    for tool in catalog.write_tools():
        stage = stage_of(tool)
        if stage and stage not in tools:
            tools = {**tools, stage: tool.name}
    missing = [w for s, w in _STAGE_WORDS.items() if s not in tools]
    return tools, ", ".join(missing)


def build_arguments(tool: McpTool, plan: Plan, stage: str) -> dict | str:
    """Arguments for the stage's tool from its schema, or why they cannot be built."""
    schema = tool.input_schema or {}
    props: Mapping[str, Any] = schema.get("properties") or {}
    values = _values(plan, stage)
    args: dict[str, Any] = {}
    for key, spec in props.items():
        value = values.get(key.casefold())
        if value is None:
            continue
        if key.casefold() == "content" and "base64" in str((spec or {}).get("description", "")).casefold():
            value = base64.b64encode(str(value).encode("utf-8")).decode("ascii")
        args = {**args, key: value}
    missing = [k for k in schema.get("required") or () if k not in args]
    if missing:
        return f"the tool needs {', '.join(missing)}, which I can't fill"
    return args


def _values(plan: Plan, stage: str) -> dict[str, Any]:
    repo = {"owner": plan.owner, "repo_owner": plan.owner, "repo": plan.repo, "repository": plan.repo,
            "repo_name": plan.repo}
    if stage == "branch":
        return {**repo, "ref": f"refs/heads/{plan.branch}", "branch": plan.branch, "branch_name": plan.branch,
                "new_branch": plan.branch, "sha": plan.base_sha, "from_branch": plan.base}
    if stage == "commit":
        out = {**repo, "path": plan.patch.path, "file_path": plan.patch.path, "content": plan.patch.new_text,
               "message": plan.message, "commit_message": plan.message, "branch": plan.branch}
        return {**out, "sha": plan.patch.file_sha} if plan.patch.file_sha else out
    return {**repo, "title": plan.title, "body": plan.body, "head": plan.branch, "base": plan.base}


async def write(deps: TurnDeps, plan: Plan, stage: str, arguments: Mapping[str, Any] | None = None) -> WriteResult:
    """The stage's one write. `arguments` re-sends an identical call after the platform's own approval."""
    if not deps.traceparent or deps.gateway is None:
        return WriteResult("not_sent", "this request has no trace context or gateway")
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure:
        return WriteResult("not_sent", failure)
    catalog = await discoverer.catalog("pr")
    tool = next((t for t in catalog.write_tools() if t.name == plan.tools.get(stage)), None)
    if tool is None:
        return WriteResult("not_sent", "the write tool is no longer granted to this agent")
    args = dict(arguments) if arguments is not None else build_arguments(tool, plan, stage)
    if isinstance(args, str):
        return WriteResult("not_sent", args)
    guarded = GuardedGateway(deps.gateway, approval(plan, stage))
    try:
        result = await guarded.call_tool(tool.name, args, deps.traceparent)
    except McpGatewayError as e:
        log.warning("write failed at the gateway: %s", type(e).__name__)
        return WriteResult("error", f"the gateway refused the call ({type(e).__name__})")
    return _settle(result, args)


def _settle(result: ToolResult, args: Mapping[str, Any]) -> WriteResult:
    if isinstance(result, (ApprovalPending, AuthorizationRequired)):
        request_id = result.hitl_request_id or ""
        if not request_id:
            return WriteResult("not_sent", "writing needs an approval the platform could not create")
        return WriteResult("paused", arguments=dict(args), hitl_request_id=request_id)
    if isinstance(result, ToolDenied):
        return WriteResult("denied" if result.by_user else "blocked", result.message)
    if result.is_error or _error_body(result.text):
        return WriteResult("error", _short(result.text))
    return WriteResult("ok", link=records.link(result.text, "html_url", "url"))


def _error_body(text: str) -> bool:
    data = records.parse_json(text)
    return isinstance(data, Mapping) and (data.get("successful") is False or bool(data.get("error")))


def _short(text: str) -> str:
    data = records.parse_json(text)
    if isinstance(data, Mapping) and data.get("error"):
        text = str(data["error"])
    return " ".join(str(text).split())[:200] or "unknown error"
