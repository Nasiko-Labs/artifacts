"""ONE approved upload: the guard checks the exact call before the gateway sees it; one approval allows one
call; a failed call is reported, never retried (a retry could create a duplicate file)."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field

import approval as a
from deps import TurnDeps
from nasiko_mcp import (
    ApprovalPending,
    AuthorizationRequired,
    McpGatewayError,
    ToolDenied,
    ToolOk,
)
from tool_health import ToolHealth, failure_text, unavailable_reason
from uploader import links

log = logging.getLogger("deck_builder.sender")


@dataclass(frozen=True)
class UploadResult:
    status: str                      # uploaded | not_sent | blocked | denied | paused | refused
    reason: str = ""
    links: tuple[str, ...] = field(default_factory=tuple)
    hitl_request_id: str | None = None


class GuardedUploader:
    """Forwards one call, only when it is exactly the approved upload. A completed call consumes the approval."""

    def __init__(self, deps: TurnDeps, approval: a.Approval):
        self._deps = deps
        self._approval: a.Approval | None = approval
        self._health = ToolHealth(deps.gateway)

    async def upload(self, data: bytes) -> UploadResult:
        approval = self._approval
        if approval is None:
            return UploadResult("refused", "this approval was already used")
        args = approval.plan.arguments(approval.file_name, base64.b64encode(data).decode("ascii"), approval.folder_id)
        return await self.call(approval.tool, args)

    async def call(self, tool: str, args: dict) -> UploadResult:
        problem = a.guard(self._approval, tool, args)
        if problem:
            log.warning("upload guard refused %s: %s", tool, problem)
            return UploadResult("refused", problem)
        try:
            result = await self._health.call_tool(tool, args, self._deps.traceparent)
        except McpGatewayError as e:
            self._approval = None
            return UploadResult("not_sent", f"the gateway refused the call ({type(e).__name__})")
        if isinstance(result, ApprovalPending):  # the platform's own card; the approval is kept for the re-call
            return UploadResult("paused", hitl_request_id=result.hitl_request_id)
        self._approval = None
        return _settled(result)


def _settled(result) -> UploadResult:
    if isinstance(result, AuthorizationRequired):
        return UploadResult("not_sent", "your drive connection needs to be re-authorized")
    if isinstance(result, ToolDenied):
        return UploadResult("denied" if result.by_user else "blocked", result.message)
    failure = failure_text(result)
    if failure is not None:
        reason = unavailable_reason(failure) or "the drive returned an error"
        return UploadResult("not_sent", reason)
    text = result.text if isinstance(result, ToolOk) else ""
    return UploadResult("uploaded", links=tuple(links(text)))
