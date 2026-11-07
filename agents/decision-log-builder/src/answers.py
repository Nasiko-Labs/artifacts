"""Pure: the pause questions (stock chat RequestCard metadata) and reading the human's answers. Empty or
unclear refusals are Skip: nothing is written by default."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal

from models import Doc
from nasiko_hitl import question_metadata

APPROVE = "Approve"
SKIP = "Skip"
CREATE_NEW = "Create a new Decision Log doc"
SHOW_TEXT = "Show text only"
MAX_OPTIONS = 10
MAX_DOC_CHOICES = 8
DOC_HEADER = "Which decision log?"
SELECT_HEADER = "Which decisions go in the log?"
SELECT_HINT = ("Pick any (none is pre-selected), or type \"log 1,3\". A line of your own becomes a note under "
               "the entries.")
WRITE_HINT = 'Answer Approve or Skip. To change it: "log 1,3" (other entries) or "note: ..." (the note line).'
EDIT_HELP = 'I can change the entries ("log 1,3") or the note ("note: ..."). Otherwise answer Approve or Skip.'
_HEADER_TITLE_MAX = 80
_YES = frozenset({"approve", "approved", "yes", "y", "ok", "okay", "append", "append it", "create", "create it",
                  "write", "write it", "go", "go ahead", "do it", "authorized"})
_NO = frozenset({"skip", "no", "n", "cancel", "stop", "don't", "dont", "deny", "denied", "reject", "rejected",
                 "decline", "declined", "don't write", "not now"})
_NOTE = re.compile(r"^\s*note\s*[:=]\s*(.*)$", re.IGNORECASE | re.DOTALL)

Decision = Literal["yes", "skip", "other"]


def decision(answer: str) -> tuple[Decision, str]:
    text = (answer or "").strip()
    word = text.casefold().rstrip(".! ")
    if not word or word in _NO:
        return "skip", ""
    if word in _YES:
        return "yes", ""
    return "other", text


def note_edit(text: str) -> str | None:
    match = _NOTE.match(text or "")
    return " ".join(match.group(1).split()) if match else None


def _short(text: str, limit: int) -> str:
    raw = text.encode("utf-8")
    return text if len(raw) <= limit else raw[:limit].decode("utf-8", "ignore") + "..."


def doc_labels(docs: Sequence[Doc], can_create: bool) -> list[str]:
    shown = list(docs)[:MAX_DOC_CHOICES]
    titles = [d.title for d in shown]
    labels = [_short(d.title if titles.count(d.title) == 1 else f"{d.title} ({d.id})", 180) for d in shown]
    return [*labels, *([CREATE_NEW] if can_create else []), SHOW_TEXT]


def doc_for(label: str | None, docs: Sequence[Doc]) -> Doc | None:
    if not label:
        return None
    return next((d for d, shown in zip(docs, doc_labels(docs, False), strict=False) if shown == label), None)


def doc_question(labels: Sequence[str]) -> dict:
    return question_metadata(DOC_HEADER, [(label, "") for label in labels], multi_select=False,
                             allow_custom_input=False)


def selection_question(labels: Sequence[str]) -> dict:
    return question_metadata(SELECT_HEADER, [(label, "") for label in labels[:MAX_OPTIONS]], multi_select=True,
                             allow_custom_input=True)


def write_question(count: int, title: str, creates: bool) -> dict:
    noun = f"{count} decision{'s' if count != 1 else ''}"
    name = _short(title, _HEADER_TITLE_MAX)
    header = f"Create '{name}' with {noun}?" if creates else f"Append {noun} to '{name}'?"
    verb = "Create the document with exactly this text." if creates else "Append exactly this text."
    return question_metadata(header, [(APPROVE, verb), (SKIP, "Don't write anything.")], multi_select=False,
                             allow_custom_input=True)


def pick(answer: str, options: Sequence[str]) -> str | None:
    """The option the answer names: exact (case-insensitive) first, then a unique partial match."""
    text = (answer or "").strip().casefold()
    exact = [o for o in options if o.casefold() == text]
    if exact:
        return exact[0]
    partial = [o for o in options if text and (text in o.casefold() or o.casefold() in text)]
    return partial[0] if len(partial) == 1 else None
