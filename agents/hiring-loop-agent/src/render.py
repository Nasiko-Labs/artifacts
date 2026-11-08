"""Pure: the debrief markdown and the output envelope, built from templates in code. Ratings are shown as
written, per interviewer in alphabetical order, with no average, total or overall. Interviewer text is
always inside double quotes; the decision-word scrub runs over the finished text. Links come only from
tool output."""

from __future__ import annotations

import json
from collections.abc import Sequence

import prompts
from analysis import alphabetical
from checks import scrub
from ratings import display
from records import Contradiction, DebriefReport, Quote

_SOURCE_PHRASE = {"pasted": "pasted scorecards", "envelope": "an earlier step's feedback", "email": "email",
                  "document": "shared documents"}
_KIND_TITLE = {"recommendation": "Recommendation", "pair": "Same interview, rated apart"}


def q(text: str) -> str:
    """An interviewer's words, marked as a quote (inner double quotes softened so the mark stays whole; a final
    full stop dropped so the sentence around it reads cleanly)."""
    flat = " ".join(text.replace('"', "'").replace("“", "'").replace("”", "'").split())
    return '"' + flat.rstrip(" .") + '"'


def _rated(name: str, rating: str) -> str:
    return f"{name} ({q(rating)})" if rating != "not stated" else f"{name} (rating not stated)"


def header(report: DebriefReport) -> str:
    who = report.candidate or "the candidate"
    role = f", {report.role}" if report.role else ""
    sources = " and ".join(_SOURCE_PHRASE.get(s, s) for s in report.sources) or "the feedback given"
    count = len(report.records)
    return (f"**Debrief draft: {who}{role}** ({count} interviewer{'' if count == 1 else 's'}; read from {sources})\n"
            f"{prompts.FRAMING_LINE}")


def markdown(report: DebriefReport) -> tuple[str, int]:
    """(the debrief, how many sentences the decision scrub replaced)."""
    sections = [header(report), _ratings(report), _agreements(report), _contradictions(report.contradictions),
                _gaps(report), _quality(report), _questions(report), _coverage(report)]
    return scrub("\n\n".join(s for s in sections if s))


def _ratings(report: DebriefReport) -> str:
    lines = ["**Ratings as written** (alphabetical, each on its own; nothing is combined)"]
    for record in alphabetical(report.records):
        interview = f", {record.interview}" if record.interview else ""
        link = f" [{record.source}]({record.source_url})" if record.source_url else ""
        lines.append(f"- {_rated(record.interviewer, display(record.rating_text))}{interview}{link}")
    return "\n".join(lines)


def _agreements(report: DebriefReport) -> str:
    if not report.agreements:
        return ""
    lines = ["**Where the feedback agrees**"]
    for agreement in report.agreements:
        rated = ", ".join(_rated(n, r) for n, r in agreement.ratings)
        lines.append(f"- {agreement.competency.capitalize()}: {rated} each cite an example. "
                     f"{q(agreement.quote.text)} ({agreement.quote.interviewer})")
    return "\n".join(lines)


def _contradictions(contradictions: Sequence[Contradiction]) -> str:
    if not contradictions:
        return "**Contradictions to resolve (0)**\nI found no contradictions between the interviewers."
    lines = [f"**Contradictions to resolve ({len(contradictions)})**"]
    for n, c in enumerate(contradictions, 1):
        lines.append(f"{n}. {_contradiction(c)}")
    return "\n".join(lines)


def _contradiction(c: Contradiction) -> str:
    title = _KIND_TITLE.get(c.kind, c.competency.capitalize())
    if c.kind == "pair":
        title = f"{title} ({c.competency})"
    ratings = dict(c.ratings)
    if c.kind == "recommendation":
        rated = ", ".join(_rated(n, r) for n, r in c.ratings)
        quotes = " ".join(_quote_line(x, None) for x in c.quotes)
        body = f"Interviewers disagree on their recommendation: {rated}. {quotes}".strip()
    else:
        body = " ".join(_quote_line(x, ratings.get(x.interviewer)) for x in c.quotes)
    return f"{title}. {body} {c.suggested_question}"


def _quote_line(quote: Quote, rating: str | None) -> str:
    who = _rated(quote.interviewer, rating) if rating else quote.interviewer
    return f"{who}: {q(quote.text)}."


def _gaps(report: DebriefReport) -> str:
    uncovered = [g.subject for g in report.gaps if g.kind == "uncovered_competency"]
    others = [g.note for g in report.gaps if g.kind != "uncovered_competency"]
    parts = []
    if uncovered:
        parts.append(f"**Not covered by anyone:** {', '.join(uncovered)}.")
    if others:
        parts.append("**Other gaps**\n" + "\n".join(f"- {note}" for note in others))
    return "\n\n".join(parts)


def _quality(report: DebriefReport) -> str:
    if not report.quality_flags:
        return ""
    lines = [f"**Feedback quality ({len(report.quality_flags)})** (word-list heuristics: prompts to ask, not findings)"]
    for flag in report.quality_flags:
        if flag.kind == "personal":
            lines.append(f"- {flag.interviewer}: {q(flag.quote)} {flag.prompt}. It is not used anywhere in this "
                         "debrief.")
        else:
            lines.append(f"- {flag.interviewer}: {q(flag.quote)} is not evidence. {flag.prompt}")
    return "\n".join(lines)


def _questions(report: DebriefReport) -> str:
    asks = [c.suggested_question for c in report.contradictions]
    asks += [f"Nobody covered {g.subject}: who can speak to it, or does it need a follow-up conversation?"
             for g in report.gaps if g.kind == "uncovered_competency"]
    asks += [f"Ask {f.interviewer} for the behaviour or answer behind {q(f.term)}."
             for f in report.quality_flags if f.kind == "vague"]
    if not asks:
        return ""
    return "**Questions for the meeting**\n" + "\n".join(f"{n}. {a}" for n, a in enumerate(dict.fromkeys(asks), 1))


def _coverage(report: DebriefReport) -> str:
    notes = [*report.coverage, *report.notes]
    return "**Coverage**\n" + "\n".join(f"- {n}" for n in notes) if notes else ""


# --- envelope ---------------------------------------------------------------------------------------------

def confidence(report: DebriefReport, any_source_failed: bool) -> str:
    dropped = report.dropped_records or sum(r.dropped_quotes for r in report.records)
    if len(report.records) < 2 or report.model_split:
        return "low"
    if dropped or any_source_failed or report.notes or len(report.records) < 3:
        return "medium"
    return "high"


def needs_human(report: DebriefReport) -> bool:
    return (any(c.kind == "recommendation" for c in report.contradictions)
            or any(f.kind == "personal" for f in report.quality_flags))


def envelope(report: DebriefReport, debrief: str, any_source_failed: bool = False, scrubbed: int = 0) -> dict:
    contradictions = [c.to_dict() for c in report.contradictions]
    summary = (f"Debrief draft for {report.candidate or 'the candidate'}: {len(report.records)} interviewers, "
               f"{len(contradictions)} contradictions, {len(report.gaps)} gaps, "
               f"{len(report.quality_flags)} feedback-quality flags. Not a recommendation.")
    return {
        "summary": summary, "items": contradictions, "confidence": confidence(report, any_source_failed),
        "needs_human": needs_human(report), "debrief_markdown": debrief, "contradictions": contradictions,
        "gaps": [g.to_dict() for g in report.gaps], "quality_flags": [f.to_dict() for f in report.quality_flags],
        "coverage": [*report.coverage, *report.notes], "candidate": report.candidate,
        "agreements": [a.to_dict() for a in report.agreements],
        "interviewers": [{"interviewer": r.interviewer, "interview": r.interview,
                          "rating_text": display(r.rating_text), "source": r.source}
                         for r in alphabetical(report.records)],
        "dropped_quotes": sum(r.dropped_quotes for r in report.records), "dropped_records": report.dropped_records,
        "decision_sentences_replaced": scrubbed,
    }


def as_json(data: dict) -> str:
    return "```json\n" + json.dumps(data, indent=2, ensure_ascii=False) + "\n```"
