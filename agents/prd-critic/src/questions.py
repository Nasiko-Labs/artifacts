"""The PRD's own open questions (read in code) and the reviewer questions the model writes, checked in
code: the quoted line must be in the PRD verbatim, and a question that repeats one of the PRD's own open
questions or a gap the rubric already reported is dropped."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from grounding import clean_quote, locate
from metrics import clean
from rubric import content_words
from sections import Layout

MAX_QUESTIONS = 8
MAX_WHY_WORDS = 15
DUPLICATE_OVERLAP = 0.6


def open_questions(lay: Layout) -> tuple[tuple[int, str], ...]:
    """(line, text) of each item in the Open questions section, verbatim minus the bullet marker."""
    items = []
    for n, line in lay.body("open_questions"):
        text = clean(line)
        if len(text.split()) >= 2 and not re.fullmatch(r"[-:| ]+", text) and not line.lstrip().startswith("#"):
            items.append((n, text))
    return tuple(items)


def overlap(a: str, b: str) -> float:
    left, right = content_words(a), content_words(b)
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def _duplicate(question: str, topics: Sequence[str]) -> bool:
    return any(overlap(question, topic) >= DUPLICATE_OVERLAP for topic in topics)


def check(data: Any, lines: Sequence[str], own: Sequence[tuple[int, str]], gaps: Sequence[dict]) -> list[dict]:
    """At most 8 reviewer questions, each with its quote located in the PRD."""
    rows = data.get("questions") if isinstance(data, dict) else None
    topics = [t for _, t in own] + [g["issue"] for g in gaps]
    kept: list[dict] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or len(kept) >= MAX_QUESTIONS:
            continue
        question = " ".join(str(row.get("question") or "").split())
        quote = clean_quote(row.get("line") or "")
        line = locate(quote, lines)
        if not question or line is None or _duplicate(question, topics):
            continue
        if any(overlap(question, k["question"]) >= DUPLICATE_OVERLAP for k in kept):
            continue
        why = " ".join(str(row.get("why") or "").split()[:MAX_WHY_WORDS])
        kept.append({"question": question, "line": " ".join(quote.split()), "line_number": line, "why": why})
    return kept
