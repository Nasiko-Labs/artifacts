"""Pure: coverage counts, confidence and needs_human (computed here, never by the model) and the output
envelope (`summary`, `items`, `confidence`, `needs_human` + `doc_markdown`, `open_questions`, `coverage`)."""

from __future__ import annotations

from collections.abc import Sequence

from checks import uncovered, unverified
from gaps import public
from grounding import tbd_count

HIGH_MAX_QUESTIONS = 3
LOW_MIN_QUESTIONS = 9
LOW_MAX_REQUIREMENTS = 4


def counts(reqs: Sequence[dict], design: dict, sections: dict, doc: str) -> dict:
    criteria_ids = {i for c in sections.get("acceptance_criteria") or () for i in c["verifies"]}
    return {"requirements": len(reqs), "components": len(design.get("components") or ()),
            "acceptance_criteria": len(sections.get("acceptance_criteria") or ()),
            "covered_by_components": len(reqs) - len(uncovered(design, reqs)),
            "with_acceptance_criteria": sum(1 for r in reqs if r["id"] in criteria_ids),
            "functional_without_criteria": len(unverified(sections, reqs)),
            "tbd_count": tbd_count(doc)}


def confidence(reqs: Sequence[dict], design: dict, sections: dict, questions: Sequence[dict], repaired: bool,
               tbds: int) -> str:
    if (len(questions) >= LOW_MIN_QUESTIONS or uncovered(design, reqs) or len(reqs) <= LOW_MAX_REQUIREMENTS):
        return "low"
    if (len(questions) <= HIGH_MAX_QUESTIONS and not repaired and not tbds and not unverified(sections, reqs)):
        return "high"
    return "medium"


def envelope(title: str, reqs: Sequence[dict], design: dict, sections: dict, questions: Sequence[dict],
             doc: str, repaired: bool, dropped: dict) -> dict:
    cov = counts(reqs, design, sections, doc)
    level = confidence(reqs, design, sections, questions, repaired, cov["tbd_count"])
    comps = design.get("components") or ()
    criteria = sections.get("acceptance_criteria") or ()
    summary = (f"Design for {title}: {len(comps)} components, {len(reqs)} requirements, {len(criteria)} acceptance "
               f"criteria, {len(questions)} open questions.")
    items = [{"kind": "open_question", "id": q["id"], "text": q["text"], "blocks": list(q["blocks"])}
             for q in questions]
    items += [{"kind": "component", "name": c["name"], "satisfies": list(c["satisfies"])} for c in comps]
    return {"summary": summary, "items": items, "doc_markdown": doc, "open_questions": public(questions),
            "requirements": [{k: r[k] for k in ("id", "type", "text", "snippet", "aliases")} for r in reqs],
            "coverage": cov, "dropped": dict(dropped), "confidence": level,
            "needs_human": bool(questions) or level == "low"}
