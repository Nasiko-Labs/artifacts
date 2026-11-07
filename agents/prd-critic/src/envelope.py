"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields).
`confidence` and `needs_human` are computed here from the run's checks, never taken from the model."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from sections import LABELS, REQUIRED, Layout

MIN_REQUIREMENTS = 3
RUBRIC_ONLY_NOTE = ("_The model checks did not finish in time, so this is the rubric-only review: no requirement "
                    "flags and no reviewer questions._")


def confidence(trace: Any, requirement_count: int) -> str:
    if trace.rubric_only or requirement_count < MIN_REQUIREMENTS:
        return "low"
    if trace.located or trace.locate_failed or trace.batch_failures or trace.questions_failed:
        return "medium"
    return "high"


def summary(score: int, band: str, gaps: Sequence[dict]) -> str:
    counts = Counter(g["severity"] for g in gaps)
    missing = sum(1 for g in gaps if g["category"] == "missing_section")
    sections = f"; {missing} section{'s' if missing != 1 else ''} missing" if missing else ""
    return (f"Score {score}/100 ({band}): {counts['blocker']} blocker{'s' if counts['blocker'] != 1 else ''}, "
            f"{counts['major']} major, {counts['minor']} minor{sections}")


def _sections_by(lay: Layout) -> str:
    kinds = {s.by for s in lay.sections.values()}
    return "model" if kinds == {"model"} else "mixed" if len(kinds) > 1 else "heading"


def _notes(trace: Any, req_count: int) -> list[str]:
    notes = list(trace.notes)
    if trace.rubric_only:
        notes.append(RUBRIC_ONLY_NOTE)
    if trace.unclassified:
        notes.append(f"_{trace.unclassified} requirements past the first 90 were checked by the vague-word list "
                     "only._")
    if trace.batch_failures:
        notes.append("_One batch of requirements could not be classified; those lines were checked by the vague-"
                     "word list only._")
    if trace.locate_failed and not trace.rubric_only:
        notes.append("_I couldn't locate the unheaded sections, so they count as missing._")
    if req_count < MIN_REQUIREMENTS:
        notes.append(f"_Only {req_count} requirement lines were parsed, so confidence is low._")
    return notes


def build(scored: dict, lay: Layout, reqs: Sequence, own: Sequence[tuple[int, str]], asked: list[dict],
          trace: Any, opts: Any) -> dict:
    gaps = scored["gaps"]
    level = confidence(trace, len(reqs))
    blockers = any(g["severity"] == "blocker" for g in gaps)
    found = [k for k in REQUIRED if k in lay.sections and lay.sections[k].present]
    return {
        "summary": summary(scored["score"], scored["band"], gaps), "items": gaps, "gaps": gaps,
        "questions": asked, "prd_questions": [text for _, text in own], "score": scored["score"],
        "band": scored["band"], "deductions": scored["deductions"], "sections_found": [LABELS[k] for k in found],
        "sections_by": _sections_by(lay), "requirements_reviewed": len(reqs), "mode":
        "lenient" if opts.lenient else "strict", "focus": opts.focus, "confidence": level,
        "needs_human": blockers or level == "low", "notes": _notes(trace, len(reqs)),
    }
