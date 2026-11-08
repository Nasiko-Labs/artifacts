"""Pure: catalog-aware templates. A template is chosen when all its required keys are present; it only
sets section order, headings and a layout hint, never content. Keys a template does not name are kept
and shown under "Other fields" (see shapes.py)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Slot:
    key: str
    heading: str
    hint: str | None = None          # "markdown" | "cards" | None
    columns: tuple[str, ...] = ()    # preferred first columns of a table


@dataclass(frozen=True)
class Template:
    name: str
    required: frozenset[str]
    slots: tuple[Slot, ...]


TEMPLATES: tuple[Template, ...] = (
    Template("summary", frozenset({"tldr", "decisions", "action_items"}), (
        Slot("tldr", "TL;DR"), Slot("decisions", "Decisions"), Slot("open_questions", "Open questions"),
        Slot("action_items", "Action items"), Slot("reversals", "Reversals"))),
    Template("promises", frozenset({"promises", "overdue"}), (
        Slot("promises", "Promises"), Slot("overdue", "Overdue"))),
    Template("notes", frozenset({"notes", "decisions", "action_items"}), (
        Slot("notes", "Notes", "markdown"), Slot("decisions", "Decisions"), Slot("action_items", "Action items"))),
    Template("ranked", frozenset({"ranked", "drop_or_delegate"}), (
        Slot("ranked", "Ranked", columns=("rank", "task", "score", "reason")),
        Slot("drop_or_delegate", "Drop or delegate"))),
    Template("pre-mortem", frozenset({"failure_modes"}), (
        Slot("failure_modes", "Failure modes", "cards"), Slot("unstated_assumptions", "Unstated assumptions"),
        Slot("questions_for_the_team", "Questions for the team"))),
    Template("draft", frozenset({"draft", "alt_shorter", "tone_notes"}), (
        Slot("draft", "Draft"), Slot("alt_shorter", "Shorter version"), Slot("tone_notes", "Tone notes"))),
    Template("memo", frozenset({"memo_markdown", "recommendation"}), (
        Slot("memo_markdown", "Memo", "markdown"), Slot("recommendation", "Recommendation"),
        Slot("reversal_triggers", "Reversal triggers"))),
    Template("translation", frozenset({"translation"}), (
        Slot("translation", "Translation"), Slot("notes", "Notes"))),
    Template("text", frozenset({"text", "ai_pattern_density"}), (
        Slot("text", "Text"), Slot("ai_pattern_density", "Density"))),
)


def match(body: dict) -> Template | None:
    """The first template whose required keys are all present, else None (the generic renderer)."""
    keys = set(body)
    return next((t for t in TEMPLATES if t.required <= keys), None)
