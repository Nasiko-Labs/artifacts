"""Pure: the open questions code adds for every gap, so they never depend on model discretion.

Gaps: no problem, no named user, no success metric (all three blocking), no deadline, no owner or team,
no non-goals, and every placeholder left in a section (one question per placeholder kind per section).
Blocking questions come first; the model's own questions follow; at most 12 in all."""

from __future__ import annotations

import re
from collections.abc import Sequence

from grounding import normalise, placeholders
from prd import LIMITS, Fact, Prd, Question

BLOCKING_SECTIONS = frozenset({"problem", "users", "metrics"})
_OWNER = re.compile(r"\b(?:owner|owned by|owns|dri|team|squad|lead by|led by)\b", re.IGNORECASE)
_DEADLINE = re.compile(
    r"\b(?:by|before|until|deadline|due|ship(?:s|ping)?|launch(?:es|ing)?)\b[^.;\n]{0,40}?\b(?:january|february|march|"
    r"april|may|june|july|august|september|october|november|december|q[1-4]|\d{4}|end of|next (?:week|month|quarter)|"
    r"monday|tuesday|wednesday|thursday|friday)\b", re.IGNORECASE)
_PLACEHOLDER_WORDS = {"[baseline?]": "baseline", "[target?]": "target", "[window?]": "time window",
                      "[ ? ]": "missing detail", "[check?]": "acceptance check"}


def mandatory(prd: Prd, facts: Sequence[Fact], corpus: str, audience: str = "") -> list[Question]:
    kinds = {f.kind for f in facts}
    out: list[Question] = []
    if not prd.problem:
        out.append(Question("What problem does this solve, and for whom? The brief doesn't state it.", "problem",
                            True))
    if "user" not in kinds and not audience and not any(not u.proposed for u in prd.users):
        out.append(Question("Who is this for? The brief names no user group.", "users", True))
    if "metric" not in kinds and not any(not m.proposed for m in prd.metrics):
        out.append(Question("Which success metric will show this worked, and what is its target?", "metrics", True))
    if "deadline" not in kinds and not _DEADLINE.search(corpus or ""):
        out.append(Question("When does this need to ship? The brief gives no deadline.", "timeline"))
    if not _OWNER.search(corpus or ""):
        out.append(Question("Which team or person owns this?", "ownership"))
    if "exclusion" not in kinds:
        out.append(Question("What is explicitly out of scope for this release?", "non_goals"))
    return out + _placeholder_questions(prd)


def _placeholder_questions(prd: Prd) -> list[Question]:
    found: dict[tuple[str, str], list[str]] = {}

    def note(section: str, label: str, *texts: str) -> None:
        for text in texts:
            for ph in placeholders(text):
                key = (section, ph if ph in _PLACEHOLDER_WORDS else "[ ? ]")
                if label not in found.setdefault(key, []):
                    found[key].append(label)

    for s in prd.goals:
        note("goals", _short(s.text), s.text)
    for r in prd.requirements:
        note("requirements", r.id, r.text, r.acceptance_hint)
    for m in prd.metrics:
        note("metrics", m.name, m.baseline, m.target, m.window)
    out = []
    for (section, ph), labels in found.items():
        word = _PLACEHOLDER_WORDS.get(ph, "missing detail")
        out.append(Question(f"What is the {word} for {', '.join(labels)}?", section, section in BLOCKING_SECTIONS))
    return out


def _short(text: str, words: int = 8) -> str:
    parts = text.split()
    return " ".join(parts[:words]) + ("..." if len(parts) > words else "")


def with_questions(prd: Prd, facts: Sequence[Fact], corpus: str, audience: str = "") -> Prd:
    """The PRD with code's mandatory questions first (blocking before the rest), then the model's, deduped."""
    model = [q for q in prd.open_questions if q.section == "general"]
    ordered = sorted(mandatory(prd, facts, corpus, audience), key=lambda q: not q.blocking) + model
    seen: set[str] = set()
    kept: list[Question] = []
    for q in ordered:
        key = normalise(q.question)
        if key not in seen:
            seen.add(key)
            kept.append(q)
    return prd.with_(open_questions=tuple(kept[:LIMITS["open_questions"]]))


def blocking(prd: Prd) -> bool:
    return any(q.blocking for q in prd.open_questions)
