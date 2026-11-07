"""Pure: a drafted reminder, reading the draft model's JSON, the code checks on its text, the approval
question for one draft, and reading the human's answer to it."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from typing import Any, Literal

from invoices import load_json_object, text_of
from nasiko_hitl import question_metadata
from send_guard import Approval

SEND = "Send"
SKIP = "Skip"
MAX_BODY_CHARS = 900
LINK_PLACEHOLDER = "[payment link]"
_SEND_WORDS = frozenset({"send", "yes", "y", "ok", "okay", "approve", "approved", "authorized", "send it"})
_SKIP_WORDS = frozenset({"skip", "no", "n", "cancel", "don't send", "dont send", "stop", "denied", "deny",
                         "reject", "rejected", "decline", "declined"})
_HEADER_MAX = 200  # bytes: the platform's limit
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"https?://[^\s<>()\"']+")
_MONEY = re.compile(r"(?<![\w.])\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?(?![\d])|(?<![\w.,])\d+\.\d{2}(?![\d])")
_FORBIDDEN = re.compile(
    r"\blate (?:payment )?fees?\b|\binterest\b|\bpenalt(?:y|ies)\b|\blegal\b|\blawyers?\b|\battorneys?\b|"
    r"\bcourt\b|\blaw ?suits?\b|\bsue\b|\bcollections? agenc(?:y|ies)\b|\bdebt collect|\bcredit (?:report|bureau|score)|"
    r"\bsuspend|\bterminat", re.IGNORECASE)

Decision = Literal["send", "skip", "edit"]


@dataclass(frozen=True)
class Draft:
    entry_id: str      # the number shown in the list
    key: str           # the stable key outcomes are recorded under
    number: str        # the invoice number
    to: str
    to_name: str
    subject: str
    body: str
    tool: str
    thread_ref: str
    level_name: str

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
    body: str
    tool: str


def parse_draft(text: str) -> DraftText | None:
    data = load_json_object(text)
    if data is None:
        return None
    body = text_of(data.get("body"))
    return DraftText(body=body, tool=text_of(data.get("tool"))) if body else None


def replace_links(body: str, hosted_url: str | None) -> str:
    """Any link that is not the checked invoice link becomes the placeholder."""
    return _URL.sub(lambda m: m.group(0) if hosted_url and m.group(0).rstrip(".,;") == hosted_url
                    else LINK_PLACEHOLDER + m.group(0)[len(m.group(0).rstrip(".,;")):], body)


def body_problem(body: str, recipient: str, amount_text: str) -> str | None:
    """Code checks on the drafted text: length, fees/threats, other addresses, other amounts."""
    if len(body) > MAX_BODY_CHARS:
        return "the draft was too long"
    if _FORBIDDEN.search(body):
        return "the draft mentioned fees, interest or consequences"
    if any(e.casefold() != recipient.casefold() for e in _EMAIL.findall(body)):
        return "the draft named another address"
    allowed = float(amount_text.split(" ")[0].replace(",", ""))
    if any(float(m.replace(",", "")) != allowed for m in _MONEY.findall(body)):
        return "the draft named an amount that isn't the invoice's"
    return None


def approval_for(draft: Draft, body: str | None = None) -> Approval:
    """The one write the human approved: this tool, this recipient, this subject, this body."""
    return Approval(action="reminder", target=f"{draft.who} (email)", draft=body or draft.body, tool=draft.tool,
                    recipient=draft.to, subject=draft.subject)


def approval_question(draft: Draft) -> tuple[str, dict]:
    """The pause text (everything that will be sent) and the structured question for the card."""
    header = f"Send reminder for {draft.number} to {draft.who}?"
    if len(header.encode("utf-8")) > _HEADER_MAX:
        header = f"Send reminder for {draft.number[:60]}?"
    hitl = question_metadata(header, [(SEND, "Send exactly this text."), (SKIP, "Don't send it; move on.")],
                             multi_select=False, allow_custom_input=True)
    return describe(draft, draft.body) + "\n\nReply Send, Skip, or type your own text to send instead.", hitl


def describe(draft: Draft, body: str) -> str:
    lines = [f"Reminder for #{draft.entry_id} ({draft.number}), level: {draft.level_name}",
             f"To: {draft.who} <{draft.to}>", f"Via: {draft.tool}", f"Subject: {draft.subject}",
             f"Thread: {draft.thread_ref or 'new email'}"]
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
