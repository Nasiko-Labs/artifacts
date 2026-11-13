"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + `runbook_markdown`, `steps`,
`placeholders`, `provenance`, `dropped`). Confidence and needs_human are computed here from code checks,
never taken from the model."""

from __future__ import annotations

import commands
import inverses
from model import INPUT, Runbook
from placeholders import found_in

LOW_DESCRIPTION_WORDS = 60
SUGGESTED_SHARE_LIMIT = 0.6
PLACEHOLDER_SOFT_LIMIT = 5


def suggested_share(runbook: Runbook) -> float:
    if not runbook.steps:
        return 1.0
    return sum(1 for s in runbook.steps if s.source != INPUT) / len(runbook.steps)


def step_texts(runbook: Runbook) -> list[tuple[str, str]]:
    """(where, text) for every rendered string, for the placeholder list."""
    out = [("title", runbook.title), ("purpose", runbook.purpose)]
    for s in runbook.steps:
        for value in (s.title, s.action, s.command, s.expect, s.if_not, s.rollback):
            out.append((f"step {s.n}", value or ""))
    return out


def placeholders(runbook: Runbook, extra: tuple[str, ...] = ()) -> list[dict]:
    texts = step_texts(runbook) + [("escalate/after", e) for e in extra]
    names = found_in(t for _, t in texts)
    return [{"name": name, "where": sorted({w for w, t in texts if name in (t or "")})} for name in names]


def confidence(runbook: Runbook, placeholder_count: int) -> str:
    if (runbook.mode == "description" and runbook.words < LOW_DESCRIPTION_WORDS) \
            or suggested_share(runbook) > SUGGESTED_SHARE_LIMIT or runbook.repair_failed:
        return "low"
    if runbook.rewritten or runbook.dropped or placeholder_count > PLACEHOLDER_SOFT_LIMIT:
        return "medium"
    return "high"


def needs_human(runbook: Runbook, level: str) -> bool:
    destructive = any(s.kind == commands.DESTRUCTIVE for s in runbook.steps)
    no_rollback = any(s.rollback and inverses.NO_ROLLBACK in s.rollback for s in runbook.steps)
    return level == "low" or destructive or no_rollback


def build(runbook: Runbook, markdown: str, extra_texts: tuple[str, ...] = ()) -> dict:
    holes = placeholders(runbook, extra_texts)
    level = confidence(runbook, len(holes))
    steps = [s.to_dict() for s in runbook.steps]
    from_input = sum(1 for s in runbook.steps if s.source == INPUT)
    summary = (f"Runbook for {runbook.service}: {len(steps)} steps ({from_input} from your text), "
               f"{len(holes)} placeholder(s) to fill in.")
    return {"summary": summary, "items": steps, "confidence": level, "needs_human": needs_human(runbook, level),
            "runbook_markdown": markdown, "title": runbook.title, "service": runbook.service, "mode": runbook.mode,
            "steps": steps, "placeholders": holes,
            "provenance": {"input": from_input, "suggested": len(steps) - from_input},
            "dropped": runbook.dropped}
