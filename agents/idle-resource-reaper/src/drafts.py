"""Pure: one owner's "delete or justify" message, assembled in code around a short model-written opening;
the code checks on that opening; the approval question for one draft; and reading the human's answer.

The resource list, the reply-by date and the closing lines are written in code, so no id, cost, date or
claim in them can be invented: the model writes only one to three greeting sentences, and those may not
say anything is safe to delete, unused or deleted, nor carry a number, a link or an address."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, fields
from datetime import date
from decimal import Decimal
from typing import Any, Literal

import pricing
import prompts
import render
from findings import Row
from nasiko_hitl import question_metadata
from owners import load_json_object, text_of
from send_guard import Approval

SEND = "Send"
SKIP = "Skip"
MAX_BODY_CHARS = 1200
MAX_OPENING_CHARS = 300
MAX_INLINE_RESOURCES = 10
_SEND_WORDS = frozenset({"send", "yes", "y", "ok", "okay", "approve", "approved", "authorized", "send it"})
_SKIP_WORDS = frozenset({"skip", "no", "n", "cancel", "don't send", "dont send", "stop", "denied", "deny",
                         "reject", "rejected", "decline", "declined"})
_HEADER_NAME_MAX = 120  # bytes; keeps the header inside the platform's 200-byte limit
_FORBIDDEN = re.compile(
    r"\bsafe(?:ly)?\s+to\s+(?:delete|remove|terminate)\b|\bsafely\b|\b(?:deleted|removed|terminated|stopped|destroyed|"
    r"cleaned up)\b|\bwill\s+(?:be\s+)?(?:delete|remove|terminate|stop|destroy)\w*|\b(?:unused|not used|not needed|"
    r"no longer needed|nobody uses|no one uses|abandoned)\b", re.IGNORECASE)
_NUMBER = re.compile(r"\d")
_LINK_OR_ADDRESS = re.compile(r"https?://|www\.|[\w.+-]+@[\w-]+\.", re.IGNORECASE)

Decision = Literal["send", "skip", "edit"]


@dataclass(frozen=True)
class Draft:
    ref: str          # the owner as shown ("priya", "Unknown owner")
    key: str          # the owner key outcomes are recorded under
    channel: str      # chat | email
    to: str           # the chat id or email the write goes to
    to_name: str
    subject: str
    body: str
    tool: str
    resources: str    # the resource ids covered, comma separated
    deadline: str

    @property
    def who(self) -> str:
        return self.to_name or self.to

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Draft:
        return Draft(**{f.name: str(data.get(f.name) or "") for f in fields(Draft)})


@dataclass(frozen=True)
class Opening:
    text: str
    tool: str


def parse_opening(text: str) -> Opening | None:
    data = load_json_object(text)
    if data is None:
        return None
    opening = text_of(data.get("opening"))
    return Opening(opening, text_of(data.get("tool"))) if opening else None


def opening_problem(opening: str) -> str | None:
    if len(opening) > MAX_OPENING_CHARS:
        return "the opening was too long"
    if _FORBIDDEN.search(opening):
        return "the opening claimed something only the owner can confirm"
    if _NUMBER.search(opening) or _LINK_OR_ADDRESS.search(opening):
        return "the opening carried a number, link or address"
    return None


def subject_for(deadline: date) -> str:
    return f"Idle cloud resources: delete or justify by {deadline.isoformat()}"


def resource_line(r: Row) -> str:
    return (f"- {r.resource_id}: {render.kind_cell(r)}, idle {render.idle_cell(r)}, "
            f"{render.cost_cell(r)}{'' if r.cost is None else '/month'}")


def compose(opening: str, rows: Sequence[Row], deadline: date) -> str:
    """The full message: opening, the resource list (at most ten, within the length cap), the total, the
    ask with the deadline and the never-deletes line."""
    total = sum((r.cost for r in rows if r.cost is not None), Decimal(0))
    ask = (f"Please reply by {deadline.isoformat()} with a reason to keep each one, or confirm it can be "
           "deleted.")
    tail = [f"Total: about {pricing.money(total)}/month.", ask, prompts.NEVER_DELETES_LINE]
    head = f"{opening}\n\nThese look idle by the checks below:"
    lines: list[str] = []
    for r in rows[:MAX_INLINE_RESOURCES]:
        candidate = [*lines, resource_line(r)]
        more = len(rows) - len(candidate)
        if len(_join(head, candidate, more, tail)) > MAX_BODY_CHARS:
            break
        lines = candidate
    return _join(head, lines, len(rows) - len(lines), tail)


def _join(head: str, lines: Sequence[str], more: int, tail: Sequence[str]) -> str:
    listing = "\n".join([*lines, *([f"and {more} more"] if more else [])])
    return "\n\n".join([head + "\n" + listing, *tail])


def approval_for(draft: Draft, body: str | None = None) -> Approval:
    """The one write the human approved: this tool, this recipient, this subject, this body."""
    return Approval(action="delete-or-justify", target=f"{draft.who} ({draft.channel})", draft=body or draft.body,
                    tool=draft.tool, recipient=draft.to, subject=draft.subject)


def approval_question(draft: Draft) -> tuple[str, dict]:
    """The pause text (everything that will be sent) and the structured question for the card."""
    name = draft.who if len(draft.who.encode("utf-8")) <= _HEADER_NAME_MAX else "this owner"
    hitl = question_metadata(f"Send this to {name} via {draft.channel}?", [
        (SEND, "Send exactly this text."),
        (SKIP, "Don't send it; move on."),
    ], multi_select=False, allow_custom_input=True)
    return describe(draft, draft.body) + "\n\nReply Send, Skip, or type your own text to send instead.", hitl


def describe(draft: Draft, body: str) -> str:
    lines = [f"Delete-or-justify message for {draft.ref}", f"To: {draft.who} <{draft.to}>",
             f"Channel: {draft.channel}", f"Via: {draft.tool}"]
    if draft.subject:
        lines.append(f"Subject: {draft.subject}")
    lines.append(f"Reply by: {draft.deadline}")
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
