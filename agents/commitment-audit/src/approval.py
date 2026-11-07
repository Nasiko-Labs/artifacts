"""Pure: the one write a rollup may become (a new document, or one chat post), the approval it binds,
the question shown for it, and reading the human's answer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

from nasiko_hitl import question_metadata
from send_guard import Approval

WRITE = "Write"
SKIP = "Skip"
_WRITE_WORDS = frozenset({"write", "write it", "yes", "y", "ok", "okay", "approve", "approved", "authorized", "save",
                          "post", "create", "send", "go"})
_SKIP_WORDS = frozenset({"skip", "no", "n", "cancel", "don't", "dont", "stop", "denied", "deny", "reject", "rejected",
                         "decline", "declined", "don't write", "dont write"})
_HEADER_NAME_MAX = 120  # bytes; keeps the header inside the platform's 200-byte limit

Kind = Literal["doc", "chat"]
Decision = Literal["write", "skip", "edit"]


@dataclass(frozen=True)
class WriteRequest:
    """Every target field is fixed by code from checked data: the title from the quarter and date, the
    channel id from a read call's output. Only the body may be edited by the human."""

    kind: str          # "doc" | "chat"
    tool: str
    body: str
    title: str = ""
    channel_id: str = ""
    channel_name: str = ""

    @property
    def target_key(self) -> str:
        return "doc" if self.kind == "doc" else f"chat:{self.channel_id}"

    @property
    def where(self) -> str:
        return f"the document \"{self.title}\"" if self.kind == "doc" else f"#{self.channel_name}"

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> WriteRequest:
        return WriteRequest(**{f.name: str(data.get(f.name) or "") for f in fields(WriteRequest)})


def approval_for(request: WriteRequest, body: str | None = None) -> Approval:
    """The one write the human approved: this tool, this target, this body."""
    if request.kind == "doc":
        return Approval(action="create_document", target=request.title, draft=body or request.body,
                        tool=request.tool, recipient="", subject=request.title)
    return Approval(action="post_message", target=f"#{request.channel_name}", draft=body or request.body,
                    tool=request.tool, recipient=request.channel_id, subject="")


def approval_question(request: WriteRequest) -> tuple[str, dict]:
    if request.kind == "doc":
        header = "Create this rollup document?"
    else:
        name = request.channel_name if len(request.channel_name.encode("utf-8")) <= _HEADER_NAME_MAX else "this channel"
        header = f"Post this rollup to #{name}?"
    hitl = question_metadata(header, [(WRITE, "Write exactly this text."), (SKIP, "Don't write it.")],
                             multi_select=False, allow_custom_input=True)
    return describe(request, request.body) + "\n\nReply Write, Skip, or type your own text to write instead.", hitl


def describe(request: WriteRequest, body: str) -> str:
    target = (f"New document: {request.title}" if request.kind == "doc"
              else f"Chat channel: #{request.channel_name} ({request.channel_id})")
    return "\n".join([target, f"Via: {request.tool}", "", body])


def interpret_answer(answer: str) -> tuple[Decision, str]:
    """Write / Skip / an edited text. Empty and unclear refusals are Skip: nothing is written by default."""
    text = (answer or "").strip()
    word = text.casefold().rstrip(".!")
    if not word or word in _SKIP_WORDS:
        return "skip", ""
    if word in _WRITE_WORDS:
        return "write", ""
    return "edit", text
