"""Pure: the finished Postmortem from the checked items, the metrics and the checked prose. Open questions for
every gap, `confidence` and `needs_human` are computed here, never taken from the model."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

import grounding as g
from merge import Merged
from metrics import Metrics, untimed_share
from records import TBD, Action, Event, Factor, Postmortem
from times import UNDATED
from write import IMPACT_NOT_QUANTIFIED, LIST_KEYS, UNTITLED, Prose

LOW_UNTIMED_SHARE = 0.5
CORE_CATEGORIES = ("impact", "mitigation", "resolution")
GAP_QUESTIONS = {
    "detection": "How was the incident detected?",
    "mitigation": "What was done to reduce the impact, and when?",
    "resolution": "When was the impact over, and how was that confirmed?",
    "trigger": "What change or event triggered the incident?",
    "impact_numbers": "How many users/requests were affected?",
    "mixed_tz": "Which timezone is each time in? The log mixes labels.",
}
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")


def has_impact_numbers(events: Sequence[Event]) -> bool:
    return any(g.tokens(_CLOCK.sub(" ", e.snippet)) for e in events if e.category == "impact")


def gap_questions(merged: Merged, metrics: Metrics, factors: Sequence[Factor], actions: Sequence[Action]) -> list[str]:
    seen = {e.category for e in merged.events}
    questions = [GAP_QUESTIONS[c] for c in ("detection", "mitigation", "resolution") if c not in seen]
    if not any(f.cls == "trigger" for f in factors):
        questions.append(GAP_QUESTIONS["trigger"])
    if not has_impact_numbers(merged.events):
        questions.append(GAP_QUESTIONS["impact_numbers"])
    if metrics.mixed:
        questions.append(GAP_QUESTIONS["mixed_tz"])
    unowned = [a.id for a in actions if a.owner == TBD]
    if unowned:
        questions.append(f"Who owns {', '.join(unowned)}?")
    return questions


def confidence(merged: Merged, metrics: Metrics, factors: Sequence[Factor], dropped: int, partial: bool) -> str:
    seen = {e.category for e in merged.events}
    if untimed_share(merged.events) >= LOW_UNTIMED_SHARE or metrics.mixed or partial \
            or any(c not in seen for c in CORE_CATEGORIES):
        return "low"
    if dropped or any(f.inferred for f in factors):
        return "medium"
    return "high"


def summary_template(events: Sequence[Event]) -> str:
    timed = [e for e in events if e.time_text]
    span = f" from {timed[0].time_text} to {timed[-1].time_text}" if len(timed) > 1 else ""
    return f"The log records {len(events)} events{span}. See the timeline below."


def impact_text(prose: Prose, events: Sequence[Event]) -> str:
    quantified = has_impact_numbers(events)
    text = prose.impact[0]
    if not text:
        recorded = [e.text.rstrip(".") for e in events if e.category == "impact"][:3]
        text = ("As recorded in the log: " + "; ".join(recorded) + ".") if recorded else ""
    if not quantified:
        text = f"{text} {IMPACT_NOT_QUANTIFIED}".strip()
    return text


def build(merged: Merged, metrics: Metrics, prose: Prose, factors: tuple[Factor, ...], title: str, severity: str,
          notes: Sequence[str], partial: bool) -> Postmortem:
    log_actions = tuple(merged.actions)
    suggested = tuple(replace(a, id=f"a{len(log_actions) + n}") for n, a in enumerate(prose.suggested, start=1))
    actions = (*log_actions, *suggested)
    templated = (not prose.summary[0]) + (not prose.impact[0] and not prose.failed)
    dropped = merged.dropped + prose.dropped + (len(merged.factors) - len(factors))
    level = confidence(merged, metrics, factors, dropped + templated, partial)
    dated = next((e.stamp[:10] for e in merged.events if e.stamp and int(e.stamp[:4]) != UNDATED.year), "")
    all_notes = [*notes, metrics.note]
    if merged.collapsed:
        all_notes.append(f"{merged.collapsed} log lines folded into neighbouring timeline rows of the same kind.")
    if merged.capped:
        all_notes.append(f"{merged.capped} more factors or action items left out to keep the lists short.")
    return Postmortem(
        title=title or prose.title or UNTITLED, severity=severity, date=dated, events=merged.events, factors=factors,
        actions=actions, metrics=dict(metrics.values), tz_label=metrics.tz_label,
        summary=prose.summary[0] or summary_template(merged.events), impact=impact_text(prose, merged.events),
        **{key: tuple(text for text, _ in prose.lists.get(key, ())) for key in LIST_KEYS},
        questions=tuple(dict.fromkeys([*gap_questions(merged, metrics, factors, actions), *prose.questions])),
        dropped=dropped, collapsed=merged.collapsed, notes=tuple(n for n in all_notes if n), confidence=level,
        needs_human=level == "low" or any(a.owner == TBD for a in actions) or not factors)


def with_actions(pm: Postmortem, actions: tuple[Action, ...]) -> Postmortem:
    """A follow-up edit: new actions, and the questions and needs_human that depend on them."""
    unowned = [a.id for a in actions if a.owner == TBD]
    kept = tuple(q for q in pm.questions if not q.startswith("Who owns "))
    questions = (*kept, f"Who owns {', '.join(unowned)}?") if unowned else kept
    return replace(pm, actions=actions, questions=questions,
                   needs_human=pm.confidence == "low" or bool(unowned) or not pm.factors)
