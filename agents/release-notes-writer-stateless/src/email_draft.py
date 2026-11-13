"""The customer email draft: one model call for subject, opening and closing; code builds the rest.

Named `email_draft` (not `email`) so it never shadows the standard library's `email` package.
Plain text. The notes are condensed to 6 items (breaking and security always kept) plus "and N more fixes
and improvements", N computed here. A link may appear only in the closing and only if the header or the
organization's instructions gave it."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import prompts
from banned import remove_banned
from grounding import free_text_problems
from inputs import Header
from llm import LlmError, TurnDeps, complete_json
from notes import LABELS, Entry, migration_note

log = logging.getLogger("release-notes.email")

SUBJECT_MAX_CHARS = 70
SUBJECT_MAX_WORDS = 8
OPENING_MAX_WORDS = 40
CLOSING_MAX_WORDS = 30
TOP_ITEMS = 6
ALWAYS_KEPT = ("breaking", "security")
FALLBACK_SUBJECT = "What's new"
FALLBACK_OPENING = "Here is what changed in this release."
FALLBACK_CLOSING = "Thank you for reading."
GREETING = "Hi [first name?],"
SIGN_OFF = "[Your name]"


@dataclass(frozen=True)
class EmailParts:
    subject: str = FALLBACK_SUBJECT
    opening: str = FALLBACK_OPENING
    closing: str = FALLBACK_CLOSING
    fallback: bool = True

    def to_dict(self) -> dict:
        return {"subject": self.subject, "opening": self.opening, "closing": self.closing, "fallback": self.fallback}

    @staticmethod
    def from_dict(data: dict | None) -> EmailParts:
        data = data or {}
        return EmailParts(str(data.get("subject") or FALLBACK_SUBJECT), str(data.get("opening") or FALLBACK_OPENING),
                          str(data.get("closing") or FALLBACK_CLOSING), bool(data.get("fallback", True)))


def _validate(data: Any) -> dict | None:
    if not isinstance(data, dict) or not all(isinstance(data.get(k), str) for k in ("subject", "opening", "closing")):
        return None
    return data


def _field(text: str, allowed: str, max_words: int, fallback: str) -> tuple[str, bool]:
    clean = remove_banned(" ".join((text or "").split()))
    bad = free_text_problems(clean, allowed, max_words)
    if bad:
        log.info("email text dropped: %s", bad)
    return (fallback, True) if bad else (clean, False)


def checked(data: dict, entries: Sequence[Entry], header: Header, policy: Sequence[str] = ()) -> EmailParts:
    notes = " ".join([*(e.change.sentence for e in entries), header.text()])
    subject, s_bad = _field(data.get("subject", ""), notes, SUBJECT_MAX_WORDS, FALLBACK_SUBJECT)
    opening, o_bad = _field(data.get("opening", ""), notes, OPENING_MAX_WORDS, FALLBACK_OPENING)
    closing, c_bad = _field(data.get("closing", ""), " ".join([notes, *policy]), CLOSING_MAX_WORDS, FALLBACK_CLOSING)
    return EmailParts(subject, opening, closing, fallback=s_bad or o_bad or c_bad)


async def ask(deps: TurnDeps, entries: Sequence[Entry], header: Header, policy: Sequence[str] = (),
              instruction: str = "") -> EmailParts:
    sentences = {e.change.id: e.change.sentence for e in entries}
    messages = prompts.email_messages(sentences, header.product, header.audience, header.tone, instruction, policy)
    try:
        data = await complete_json(deps, messages, prompts.EMAIL_CONTRACT, _validate)
    except LlmError as e:
        log.warning("email call failed: %s", e)
        return EmailParts()
    return checked(data, entries, header, policy)


def subject_line(parts: EmailParts, header: Header) -> str:
    prefix = " ".join(x for x in (header.product, header.version) if x)
    subject = f"{prefix}: {parts.subject}" if prefix else parts.subject
    return subject if len(subject) <= SUBJECT_MAX_CHARS else subject[: SUBJECT_MAX_CHARS - 3].rstrip() + "..."


def condensed(entries: Sequence[Entry]) -> tuple[list[Entry], int]:
    """Breaking and security items always; then the rest in notes order up to 6 items in all."""
    kept = [e for e in entries if e.block in ALWAYS_KEPT]
    room = max(0, TOP_ITEMS - len(kept))
    others = [e for e in entries if e.block not in ALWAYS_KEPT]
    chosen = {e.number for e in kept + others[:room]}
    return [e for e in entries if e.number in chosen], len(entries) - len(chosen)


def _line(entry: Entry) -> str:
    change = entry.change
    return f"- {change.sentence.rstrip('.')}." + migration_note(change)


def build(parts: EmailParts, entries: Sequence[Entry], header: Header) -> str:
    shown, more = condensed(entries)
    lines = [f"Subject: {subject_line(parts, header)}", "", GREETING, "", parts.opening]
    block = None
    for entry in shown:
        if entry.block != block:
            block = entry.block
            lines += ["", f"{LABELS[block]}:"]
        lines.append(_line(entry))
    if more:
        lines += ["", f"...and {more} more fixes and improvements."]
    lines += ["", parts.closing, "", SIGN_OFF]
    return "\n".join(lines)
