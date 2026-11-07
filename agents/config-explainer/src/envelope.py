"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + `explanation`, `gotchas`,
`kind`, `facts`). `confidence` and `needs_human` are computed here from the checks, never from the model."""

from __future__ import annotations

from analysis import Analysis
from explain import Explained
from gotchas import has_high


def confidence(analysis: Analysis, explained: Explained | None, certain: bool) -> str:
    if analysis.parse_error or analysis.unsupported:
        return "low"
    if explained is not None and (explained.status != "ok" or explained.filled):
        return "low"
    if analysis.kind == "cron":
        return "high"
    shaky = explained is not None and (explained.repaired or explained.removed_sentences > 0)
    if not certain or analysis.partial or shaky:
        return "medium"
    return "high"


def needs_human(analysis: Analysis) -> bool:
    return bool(analysis.parse_error) or analysis.unsupported or has_high(analysis.gotchas)


def build(analysis: Analysis, explained: Explained | None, certain: bool) -> dict:
    notes = explained.notes if explained is not None else {}
    gotchas = [g.to_dict(notes.get(g.id, "")) for g in analysis.gotchas]
    if analysis.kind == "cron" or explained is None or explained.status != "ok":
        text = analysis.explanation
    else:
        text = explained.explanation
    facts = dict(analysis.facts)
    if explained is not None and explained.examples is not None:
        facts["verified_examples"] = explained.examples
    return {"summary": analysis.summary, "items": [{"kind": "gotcha", **g} for g in gotchas],
            "explanation": text, "gotchas": gotchas, "kind": analysis.kind, "facts": facts,
            "checked": list(analysis.checked), "parse_error": analysis.parse_error,
            "confidence": confidence(analysis, explained, certain), "needs_human": needs_human(analysis)}
