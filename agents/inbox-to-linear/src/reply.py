"""The reply: drafted by the model with the {{ISSUE_LINK}} token (repaired once when the token is missing),
the real link put in by code; recipient, thread and subject come from the email read, never the model;
sent only through the send guard bound to the approved text."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any

import prompts
import send_guard
from deps import TurnDeps
from llmcall import ask_once
from models import Request
from nasiko_mcp import McpTool
from send_guard import Approval
from sender import WriteResult, resume_write, write_once

MAX_REPLY_WORDS = 60
FALLBACK = f"Thanks, this is now tracked: {prompts.ISSUE_LINK_TOKEN}"


@dataclass(frozen=True)
class ReplyDraft:
    request_id: str
    to: str
    to_name: str
    thread_id: str
    message_id: str
    subject: str
    body: str
    issue_key: str
    issue_url: str

    @property
    def who(self) -> str:
        return self.to_name or self.to

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> ReplyDraft:
        return ReplyDraft(**{f.name: str(data.get(f.name) or "") for f in fields(ReplyDraft)})


def link_text(key: str, url: str) -> str:
    if key and url:
        return f"{key} ({url})"
    return url or key


def subject_for(request: Request) -> str:
    subject = request.subject.strip()
    return subject if subject.casefold().startswith("re:") or not subject else f"Re: {subject}"


async def draft(deps: TurnDeps, request: Request, key: str, url: str) -> ReplyDraft:
    messages = ({"role": "system", "content": prompts.reply_system()},
                {"role": "user", "content": prompts.fence_emails(f"From: {request.who}\nSubject: {request.subject}\n"
                                                                 f"Ask: {request.ask_quote}")})
    text = await ask_once(deps, messages)
    if not _has_token(text):
        text = await ask_once(deps, (*messages, {"role": "assistant", "content": text},
                                     {"role": "user", "content": prompts.REPLY_TOKEN_REPAIR}))
    if not _has_token(text) or len(text.split()) > MAX_REPLY_WORDS:
        text = FALLBACK
    body = text.strip().replace(prompts.ISSUE_LINK_TOKEN, link_text(key, url))
    return ReplyDraft(request_id=request.id, to=request.sender, to_name=request.sender_name,
                      thread_id=request.thread_id, message_id=request.message_id, subject=subject_for(request),
                      body=body, issue_key=key, issue_url=url)


def _has_token(text: str) -> bool:
    return text.count(prompts.ISSUE_LINK_TOKEN) == 1


def approval_for(reply: ReplyDraft, tool: str, body: str | None = None) -> Approval:
    return Approval(action="reply", target=reply.who, draft=body or reply.body, tool=tool, recipient=reply.to,
                    subject=reply.subject)


async def send(deps: TurnDeps, tool: McpTool, reply: ReplyDraft, approval: Approval) -> WriteResult:
    messages = ({"role": "system", "content": prompts.send_system()},
                {"role": "user", "content": _send_request(tool, reply, approval)})
    return await write_once(deps, tool, messages, _check(approval))


async def resume_send(deps: TurnDeps, tool: McpTool, approval: Approval, loop: Mapping, answer: str) -> WriteResult:
    return await resume_write(deps, tool, loop, answer, _check(approval))


def _check(approval: Approval):
    def check(name: str, arguments: Mapping[str, Any]) -> str | None:
        decision = send_guard.check([name], [dict(arguments)], approval)
        if not decision.allowed or not decision.is_write:
            return decision.reason or "Refused: only the approved reply may be sent here."
        return None
    return check


def _send_request(tool: McpTool, reply: ReplyDraft, approval: Approval) -> str:
    return (f"Send with the tool {tool.name}.\nRecipient: {approval.recipient}\nSubject: {approval.subject}\n"
            f"Reply in thread: {reply.thread_id or 'none'} (original message id: {reply.message_id or 'none'})\n"
            f"Message body:\n{prompts.fence_approved(approval.draft)}")
