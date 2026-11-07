"""Pure: a drafted outreach email, reading the draft model's JSON, the code checks on its text, the
approval question for one draft, and reading the human's answer to it."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

import outreach
from checks import normalise_text
from nasiko_hitl import question_metadata
from records import load_json_object, text_of
from scoring import Row
from send_guard import Approval

SEND = "Send"
SKIP = "Skip"
MAX_BODY_CHARS = 900
_SEND_WORDS = frozenset({"send", "yes", "y", "ok", "okay", "approve", "approved", "authorized", "send it"})
_SKIP_WORDS = frozenset({"skip", "no", "n", "cancel", "don't send", "dont send", "stop", "denied", "deny",
                         "reject", "rejected", "decline", "declined"})
_HEADER_NAME_MAX = 120  # bytes; keeps the header inside the platform's 200-byte limit
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_OFFERS = re.compile(r"\b(?:discount|\d+\s*% off|percent off|credit|coupon|promo(?:tion)?(?: code)?|free month|"
                     r"months? free|refund|waive|rebate|complimentary)\b", re.IGNORECASE)
_COMPLAINT_TALK = re.compile(r"\b(?:complain\w*|frustrat\w*|unhappy|disappoint\w*|(?:you|your team) "
                             r"(?:raised|reported|mentioned|flagged))\b", re.IGNORECASE)
_PREDICTION = re.compile(r"\b(?:risk score|churn(?:ing)?|likely to (?:leave|cancel))\b", re.IGNORECASE)
_QUOTED = re.compile(r"[\"“]([^\"”]{12,})[\"”]")

Decision = Literal["send", "skip", "edit"]


@dataclass(frozen=True)
class Draft:
    row_id: str      # the number shown in the list
    row_key: str     # the account id outcomes are recorded under
    to: str
    to_name: str
    subject: str
    body: str
    tool: str
    thread_ref: str
    link: str
    outreach: str

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


def body_problem(row: Row, to: str, text: DraftText) -> str | None:
    """Code checks on the drafted text. Each failure names what was wrong; nothing is sent."""
    body = text.body
    if len(body) > MAX_BODY_CHARS:
        return "the draft was too long"
    if _OFFERS.search(body) or _OFFERS.search(text.subject):
        return "the draft offered a discount or credit"
    if any(e.casefold() != to.casefold() for e in _EMAIL.findall(body)):
        return "the draft named another address"
    if _PREDICTION.search(body):
        return "the draft talked about churn risk"
    if "complaint" not in row.account.kinds and _COMPLAINT_TALK.search(body):
        return "the draft mentioned a complaint that isn't on record"
    evidence = normalise_text(" ".join([row.account.complaint_subject, row.account.reason,
                                        *(s.evidence for s in row.account.signals)]))
    if any(normalise_text(q) not in evidence for q in _QUOTED.findall(body)):
        return "the draft quoted something that isn't on record"
    return None


def draft_for(row: Row, to: str, text: DraftText) -> Draft:
    """Recipient and thread come from checked data (or the address the user typed), never the model."""
    return Draft(row_id=row.id, row_key=row.key, to=to, to_name=row.account.name, subject=text.subject,
                 body=text.body, tool=text.tool, thread_ref=row.account.reply_thread, link=row.account.reply_link,
                 outreach=row.outreach)


def approval_for(draft: Draft, body: str | None = None) -> Approval:
    """The one write the human approved: this tool, this recipient, this subject, this body."""
    return Approval(action="outreach", target=f"{draft.who} (email)", draft=body or draft.body,
                    tool=draft.tool, recipient=draft.to, subject=draft.subject)


def approval_question(draft: Draft) -> tuple[str, dict]:
    """The pause text (everything that will be sent) and the structured question for the card."""
    name = draft.who if len(draft.who.encode("utf-8")) <= _HEADER_NAME_MAX else "this account"
    hitl = question_metadata(f"Send outreach to {name}?", [
        (SEND, "Send exactly this email."),
        (SKIP, "Don't send it; move on."),
    ], multi_select=False, allow_custom_input=True)
    return describe(draft, draft.body) + "\n\nReply Send, Skip, or type your own text to send instead.", hitl


def describe(draft: Draft, body: str) -> str:
    lines = [f"Outreach for #{draft.row_id} ({outreach.LABELS.get(draft.outreach, draft.outreach)})",
             f"To: {draft.who} <{draft.to}>", f"Via: {draft.tool}", f"Subject: {draft.subject}",
             f"Thread: {draft.thread_ref or 'new email'}", f"Outreach type: {draft.outreach}"]
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
