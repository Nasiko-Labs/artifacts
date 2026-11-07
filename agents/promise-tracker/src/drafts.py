"""Pure: a drafted reply, reading the draft model's JSON, the approval question for one draft, and
reading the human's answer to it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

from nasiko_hitl import question_metadata
from promises import Promise, load_json_object, text_of
from send_guard import Approval

SEND = "Send"
SKIP = "Skip"
_SEND_WORDS = frozenset({"send", "yes", "y", "ok", "okay", "approve", "approved", "authorized", "send it"})
_SKIP_WORDS = frozenset({"skip", "no", "n", "cancel", "don't send", "dont send", "stop", "denied", "deny",
                         "reject", "rejected", "decline", "declined"})
_HEADER_NAME_MAX = 120  # bytes; keeps the header inside the platform's 200-byte limit

Decision = Literal["send", "skip", "edit"]


@dataclass(frozen=True)
class Draft:
    promise_id: str
    channel: str
    to: str
    to_name: str
    subject: str
    body: str
    tool: str
    thread_ref: str
    link: str
    topic: str

    @property
    def who(self) -> str:
        return self.to_name or self.to

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Draft:
        return Draft(**{f.name: str(data.get(f.name) or "") for f in fields(Draft)})


@dataclass(frozen=True)
class DraftText:
    """What the draft model wrote, before code decides whether it can be sent."""

    subject: str
    body: str
    tool: str


def parse_draft(text: str) -> DraftText | None:
    data = load_json_object(text)
    if data is None:
        return None
    body = text_of(data.get("body"))
    if not body:
        return None
    return DraftText(subject=text_of(data.get("subject")), body=body, tool=text_of(data.get("tool")))


def draft_for(promise: Promise, text: DraftText, tool: str) -> Draft:
    """Recipient, channel and thread come from the checked promise, never from the draft model."""
    return Draft(promise_id=promise.id, channel=promise.source, to=promise.to, to_name=promise.to_name,
                 subject=text.subject if promise.source == "email" else "", body=text.body, tool=tool,
                 thread_ref=promise.thread_ref, link=promise.link, topic=promise.topic)


def approval_for(draft: Draft, body: str | None = None) -> Approval:
    """The one write the human approved: this tool, this recipient, this subject, this body."""
    return Approval(action="reply", target=f"{draft.who} ({draft.channel})", draft=body or draft.body,
                    tool=draft.tool, recipient=draft.to, subject=draft.subject)


def approval_question(draft: Draft) -> tuple[str, dict]:
    """The pause text (everything that will be sent) and the structured question for the card."""
    name = draft.who if len(draft.who.encode("utf-8")) <= _HEADER_NAME_MAX else "this person"
    header = f"Send this reply to {name}?"
    hitl = question_metadata(header, [
        (SEND, "Send exactly this text."),
        (SKIP, "Don't send it; move on."),
    ], multi_select=False, allow_custom_input=True)
    return describe(draft, draft.body) + "\n\nReply Send, Skip, or type your own text to send instead.", hitl


def describe(draft: Draft, body: str) -> str:
    lines = [f"Reply for #{draft.promise_id} ({draft.topic})", f"To: {draft.who} <{draft.to}>",
             f"Channel: {draft.channel}, thread {draft.thread_ref or 'new'}", f"Via: {draft.tool}"]
    if draft.subject:
        lines.append(f"Subject: {draft.subject}")
    return "\n".join([*lines, "", body])


def interpret_answer(answer: str) -> tuple[Decision, str]:
    """Send / Skip / an edited text. Empty and unclear refusals are Skip: nothing goes out by default."""
    text = (answer or "").strip()
    word = text.casefold().rstrip(".!")
    if not word or word in _SKIP_WORDS:
        return "skip", ""
    if word in _SEND_WORDS:
        return "send", ""
    return "edit", text
