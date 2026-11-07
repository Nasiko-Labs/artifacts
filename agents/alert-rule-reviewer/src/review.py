"""One review: parse -> rules (code) -> label (model, batched) -> AR008 (code) -> checks -> changes (code).

    intake -> detect+parse -> rules -> label -> merge -> changes -> envelope

The call cap for the turn is 2 + ceil(alerts / 30), at most 12."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import changes as changes_step
import findings as findings_step
import label as label_step
import merge
import parse as parse_step
import rules_am
import rules_cross
import rules_prom
import rules_vendor
from alerts import GRAFANA, Alert, Parsed
from detect import Settings, Split
from envelope import Outcome
from llm import MAX_CALLS_PER_TURN, TurnDeps

SNIPPET_MAX_LINES = 30


@dataclass(frozen=True)
class Review:
    outcome: Outcome | None       # None: nothing could be parsed
    parsed: Parsed


def call_cap(alerts: int) -> int:
    return min(MAX_CALLS_PER_TURN, 2 + math.ceil(alerts / label_step.BATCH_SIZE))


async def run(deps: TurnDeps, split: Split, settings: Settings, policy: Sequence[str]) -> Review:
    parsed = parse_step.parse(split)
    if parsed.empty:
        return Review(None, parsed)
    found = [*rules_prom.check(parsed, settings), *rules_cross.check(parsed), *rules_am.check(parsed),
             *rules_vendor.check(parsed)]
    deps.budget.max_calls = call_cap(len(parsed.alerts))
    labels = await label_step.label(deps, parsed.alerts, policy)
    found += merge.cause_paging(parsed, labels)
    paste = "\n".join(parsed.files.values())
    checked, dropped = findings_step.finalize(found, paste)
    proposed, dropped_changes = changes_step.build(checked, parsed, settings.owner_label)
    outcome = Outcome(parsed=parsed, findings=checked, changes=proposed, labels=labels.counts(),
                      label_failed=len(labels.failed), dropped_findings=dropped, dropped_changes=dropped_changes,
                      not_checked=_not_checked(parsed, settings, labels))
    return Review(outcome, parsed)


def _not_checked(parsed: Parsed, settings: Settings, labels: label_step.Labels) -> list[str]:
    notes = list(parsed.skipped)
    if labels.failed:
        why = "the turn deadline was reached" if labels.deadline_hit else "the labelling call failed"
        notes.append(f"Cause-based paging (AR008) for {len(labels.failed)} alert(s): {why}.")
    if any(a.vendor == GRAFANA for a in parsed.alerts):
        notes.append("Grafana-managed rules: expression checks (for, windows, thresholds); only severity, owner, "
                     "runbook and duplicates were checked.")
    if rules_am.tier_pairs(parsed) and not parsed.am:
        notes.append("Inhibit rules for warning/critical pairs (AM004): no Alertmanager config was pasted.")
    if settings.scrape_assumed and any(a.vendor == "prometheus" for a in parsed.alerts):
        notes.append("Scrape interval not stated: assumed 15s.")
    return notes + list(parsed.notes)


def snippet(parsed: Parsed, alert: Alert) -> str:
    """The alert's own lines, verbatim: from its first line to the next alert in the same file."""
    lines = parsed.files.get(alert.file, "").splitlines()
    starts = sorted(a.line for a in parsed.alerts if a.file == alert.file and a.line > alert.line)
    end = min(starts[0] - 1 if starts else len(lines), alert.line - 1 + SNIPPET_MAX_LINES)
    body = lines[alert.line - 1:end]
    if body and body[0].lstrip().startswith("- "):
        indent = len(body[0]) - len(body[0].lstrip())
        cut = next((i for i, line in enumerate(body[1:], 1) if line.strip() and len(line) - len(line.lstrip())
                    <= indent), len(body))
        body = body[:cut]
    while body and not body[-1].strip():
        body.pop()
    return "\n".join(body)
