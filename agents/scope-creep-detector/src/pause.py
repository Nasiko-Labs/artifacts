"""The one pause this agent makes: "Which team?" as a single-select stock chat question. The block builder
is adapted from the MCP kit's nasiko_hitl.question_metadata (approval pauses are not needed: read-only).

The platform silently drops a malformed block and falls back to a plain question, so this validates
against the same limits (UTF-8 bytes, as the platform measures them) and raises."""

from __future__ import annotations

from collections.abc import Sequence

import prompts

MAX_OPTIONS = 10
MAX_OPTION_LABEL_LEN = 200
MAX_QUESTION_HEADER_LEN = 200
PAUSE_KEY = "scope_creep_team"


def question_metadata(header: str, labels: Sequence[str], allow_custom_input: bool = True) -> dict:
    clean_header = header.strip()
    if not clean_header or _bytes(clean_header) > MAX_QUESTION_HEADER_LEN:
        raise ValueError(f"header must be 1-{MAX_QUESTION_HEADER_LEN} UTF-8 bytes")
    if not labels or len(labels) > MAX_OPTIONS:
        raise ValueError(f"options must have 1-{MAX_OPTIONS} entries, got {len(labels)}")
    seen: set[str] = set()
    options = []
    for label in labels:
        clean = label.strip()
        if not clean or _bytes(clean) > MAX_OPTION_LABEL_LEN or clean in seen:
            raise ValueError(f"bad option label: {label!r}")
        seen.add(clean)
        options.append({"label": clean})
    return {"header": clean_header, "options": options, "multi_select": False,
            "allow_custom_input": bool(allow_custom_input)}


def team_labels(teams: Sequence[str]) -> tuple[str, ...]:
    """Usable option labels: trimmed, unique, within the byte limit, at most MAX_OPTIONS."""
    out: list[str] = []
    for team in teams:
        clean = team.strip()
        if clean and clean not in out and _bytes(clean) <= MAX_OPTION_LABEL_LEN:
            out.append(clean)
    return tuple(out[:MAX_OPTIONS])


def team_pause(teams: Sequence[str], query: str) -> dict:
    """{"text", "hitl", "metadata"}: the question text, its status-message block, and the task metadata
    that the resume reads back (the original request and the offered teams)."""
    labels = team_labels(teams)
    return {"text": f"{prompts.TEAM_QUESTION} I can see these teams: {', '.join(labels)}.",
            "hitl": question_metadata(prompts.TEAM_QUESTION, labels),
            "metadata": {PAUSE_KEY: {"query": query, "teams": list(labels)}}}


def match_team(answer: str, teams: Sequence[str]) -> str | None:
    """The team the answer names: the exact label, a case-insensitive match, or the one label that
    contains (or is contained in) the typed text."""
    text = " ".join((answer or "").split())
    if text in teams:
        return text
    folded = text.casefold()
    exact = [t for t in teams if t.casefold() == folded]
    if exact:
        return exact[0]
    if not folded:
        return None
    partial = [t for t in teams if folded in t.casefold() or t.casefold() in folded]
    return partial[0] if len(partial) == 1 else None


def _bytes(text: str) -> int:
    return len(text.encode("utf-8"))
