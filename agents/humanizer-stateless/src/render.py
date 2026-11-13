"""Pure: every reply is rendered here, in code, from checked results only."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import prompts
import smalltalk
from outcome import CLEAN, ENGLISH_ONLY, FAILED_CHECK, MODEL_FAILED, OFF_TOPIC, Outcome
from patterns_phrases import CATEGORIES, LABELS

HINTS = 'Say "show the changes", "keep my em dashes", "more casual".'
MAX_LISTED_PHRASES = 5


def outcome(result: Outcome, notes: Sequence[str] = ()) -> str:
    if result.status == CLEAN:
        body = prompts.ALREADY_NATURAL.format(density=result.before)
    elif result.status == OFF_TOPIC:
        body = smalltalk.OFF_TOPIC_REPLY
    elif result.status == ENGLISH_ONLY:
        body = prompts.ENGLISH_ONLY
    elif result.status == MODEL_FAILED:
        body = prompts.MODEL_FAILED
    elif result.status == FAILED_CHECK:
        body = f"{prompts.CHECK_FAILED.format(reason=result.reason or 'a check failed')}\n\n{result.text}"
    else:
        body = f"{result.text.rstrip()}\n\n{footer(result)}"
    return "\n\n".join(p for p in (body, *notes) if p)


def footer(result: Outcome) -> str:
    removed = sum(result.removed.values())
    parts = [f"AI-pattern density {result.before} -> {result.after} per 100 words.",
             f"{removed} {'pattern' if removed == 1 else 'patterns'} removed{_breakdown(result.removed)}."]
    if result.cut_in_code:
        parts.append("The model's shorter version was still over the word limit, so I cut filler clauses and "
                     "sentences in code.")
    parts.append("Facts, numbers and links unchanged.")
    if result.confidence == "low":
        parts.append("Some AI patterns remain; review it before sending.")
    parts.append(HINTS)
    return "_" + " ".join(parts) + "_"


def _breakdown(removed: Mapping[str, int]) -> str:
    ordered = sorted(removed.items(), key=lambda kv: (-kv[1], CATEGORIES.index(kv[0])))
    return f" ({', '.join(f'{n} {LABELS[c]}' for c, n in ordered)})" if ordered else ""


def envelope_text(outcomes: Mapping[str, Outcome], notes: Sequence[str] = ()) -> str:
    """The primary field rendered like a free-text draft; the other fields are named."""
    names = list(outcomes)
    first = outcome(outcomes[names[0]])
    others = f"Also humanized: {', '.join(names[1:])} (say \"as json\" to see every field)." if names[1:] else ""
    return "\n\n".join(p for p in (first, others, *notes) if p)


def changes(result: Outcome | None) -> str:
    if result is None:
        return prompts.NOTHING_STORED
    if not result.changes:
        lead = "No itemized changes were recorded for the last pass."
    else:
        rows = [f'{n}. {LABELS.get(c["category"], c["category"])}: "{c["before"]}" -> '
                + (f'"{c["after"]}"' if c["after"] else "(cut)") for n, c in enumerate(result.changes, start=1)]
        lead = "**Changes**\n" + "\n".join(rows)
    note = f"\n\n_{result.dropped} listed change(s) left out: their wording wasn't found in the text._" \
        if result.dropped else ""
    return lead + note


def patterns_removed(result: Outcome | None) -> str:
    if result is None:
        return prompts.NOTHING_STORED
    if not result.found:
        return f"I found no AI patterns in the last draft (density {result.before})."
    rows = []
    for category in CATEGORIES:
        phrases = result.found.get(category)
        if not phrases:
            continue
        listed = ", ".join(f'"{p}"' for p in phrases[:MAX_LISTED_PHRASES])
        gone = result.removed.get(category, 0)
        rows.append(f"- {LABELS[category]}: {listed} ({gone} removed)")
    return (f"**Patterns found** (density {result.before} -> {result.after} per 100 words)\n" + "\n".join(rows))
