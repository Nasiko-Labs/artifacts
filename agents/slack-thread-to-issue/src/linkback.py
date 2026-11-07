"""The link-back: a fixed "Tracked as KEY: URL" reply built in code (no model words go into a thread), offered
only after the issue exists, posted only through the send guard bound to the approved tool, the channel, the
thread and the exact text (the user may edit the text; the guard then binds the edited text)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any

import prompts
import send_guard
from deps import TurnDeps
from models import Finding
from nasiko_mcp import McpTool
from send_guard import Approval
from sender import WriteResult, resume_write, write_once

_THREAD_TOKENS = frozenset({"thread", "ts"})
_BROADCAST_TOKENS = frozenset({"broadcast"})


@dataclass(frozen=True)
class LinkBack:
    finding_id: str
    channel: str
    channel_id: str
    thread_ts: str
    text: str
    issue_key: str
    issue_url: str

    @property
    def where(self) -> str:
        return f"#{self.channel}" if self.channel else (self.channel_id or "the channel")

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> LinkBack:
        return LinkBack(**{f.name: str(data.get(f.name) or "") for f in fields(LinkBack)})


def template(key: str, url: str) -> str:
    """The fixed reply text, with only the tracker's own key and link in it."""
    if key and url:
        return f"Tracked as {key}: {url}"
    return f"Tracked as {key}" if key else f"Tracked: {url}"


def draft(finding: Finding, key: str, url: str) -> LinkBack:
    return LinkBack(finding_id=finding.id, channel=finding.channel, channel_id=finding.channel_id,
                    thread_ts=finding.thread_ts, text=template(key, url), issue_key=key, issue_url=url)


def approval_for(link: LinkBack, tool: str, text: str | None = None) -> Approval:
    body = text or link.text
    return Approval(action="post", target=f"{link.where} thread {link.thread_ts}", draft=body, tool=tool,
                    recipient=link.channel_id, subject="")


def check(link: LinkBack, approval: Approval):
    """The send guard (tool, channel, exact text) plus the thread: every thread field must name the approved
    thread, at least one must, and nothing may broadcast it to the whole channel."""
    def guard(name: str, arguments: Mapping[str, Any]) -> str | None:
        decision = send_guard.check([name], [dict(arguments)], approval)
        if not decision.allowed or not decision.is_write:
            return decision.reason or "Refused: only the approved reply may be posted here."
        return _thread_problem(arguments, link)
    return guard


def _thread_problem(arguments: Mapping[str, Any], link: LinkBack) -> str | None:
    pairs = [(k, v) for k, v in _flat(arguments)]
    threads = [str(v).strip() for k, v in pairs if _THREAD_TOKENS & set(send_guard.name_tokens(k))
               and not isinstance(v, bool)]
    if not threads or any(t != link.thread_ts for t in threads):
        return f"Refused: the reply must go in the approved thread ({link.thread_ts}) and nowhere else."
    if any(v is True for k, v in pairs if _BROADCAST_TOKENS & set(send_guard.name_tokens(k))):
        return "Refused: the reply must stay in the thread, not be broadcast to the channel."
    return None


def _flat(value: object, key: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, Mapping):
        return [p for k, v in value.items() for p in _flat(v, str(k))]
    if isinstance(value, (list, tuple)):
        return [p for v in value for p in _flat(v, key)]
    return [(key, value)]


async def post(deps: TurnDeps, tool: McpTool, link: LinkBack, approval: Approval) -> WriteResult:
    messages = ({"role": "system", "content": prompts.post_system()},
                {"role": "user", "content": _post_request(tool, link, approval)})
    return await write_once(deps, tool, messages, check(link, approval))


async def resume_post(deps: TurnDeps, tool: McpTool, link: LinkBack, approval: Approval, loop: Mapping,
                      answer: str) -> WriteResult:
    return await resume_write(deps, tool, loop, answer, check(link, approval))


def _post_request(tool: McpTool, link: LinkBack, approval: Approval) -> str:
    return (f"Post with the tool {tool.name}.\nChannel: {link.channel_id}\nThread timestamp: {link.thread_ts}\n"
            f"Message text:\n{prompts.fence_approved(approval.draft)}")
