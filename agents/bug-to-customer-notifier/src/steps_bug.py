"""The bug step: read the named bug(s), or sweep for bugs that reached a done status in the window.
A bounded tool loop over tracker read tools; its answer is post-checked in code (checks.check_bugs)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import checks
import coverage
import prompts
import records
import replies
import smalltalk
from deps import TurnDeps
from discovery import discover
from reader import TurnReader
from records import Bug

BUG_LOOP_CALLS = 4  # plus one JSON repair = 5
_KEY = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{1,9}-\d+)(?![A-Za-z0-9])")
_DAYS = re.compile(r"\b(\d{1,3})\s*(?:-\s*)?days?\b", re.IGNORECASE)
_WEEKS = re.compile(r"\b(\d{1,2}|one|two|three|four|a)\s*weeks?\b", re.IGNORECASE)
_WORD_WEEKS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4}


@dataclass(frozen=True)
class BugOutcome:
    """Either a reply that ends the turn, one bug to continue with, or (sweep) bugs to pick from."""

    reply: str | None = None
    bug: Bug | None = None
    choices: tuple[Bug, ...] = ()
    no_fix_version: tuple[Bug, ...] = ()
    window_days: int = 0
    notes: tuple[str, ...] = ()


def requested_keys(query: str) -> tuple[str, ...]:
    """Issue keys named in the message (`PROJ-482`), in order, each once."""
    return tuple(dict.fromkeys(k for k in _KEY.findall(query or "")))


def window_days(query: str, default: int, most: int) -> int:
    days = _DAYS.search(query or "")
    if days:
        return max(1, min(most, int(days.group(1))))
    weeks = _WEEKS.search(query or "")
    if weeks:
        word = weeks.group(1).lower()
        return max(1, min(most, 7 * (_WORD_WEEKS.get(word) or int(word))))
    return default


async def bug_step(deps: TurnDeps, turn: TurnReader, query: str) -> BugOutcome:
    catalog, failure = await discover(deps, "bug")
    if failure is not None:
        return BugOutcome(failure)
    tools = catalog.read_tools()
    if not coverage.assess("tracker", tools, catalog.source_of).ok:
        return BugOutcome(replies.NO_TRACKER_TOOL)  # zero LLM calls after discovery
    keys = requested_keys(query)
    days = window_days(query, deps.config.sweep_days, deps.config.max_sweep_days)
    w = prompts.window(deps.today, days)
    run = await turn.run_json(deps, tools, prompts.bug_system(w, keys), query, BUG_LOOP_CALLS, records.parse_bugs)
    tracker = coverage.assess("tracker", tools, catalog.source_of, turn.health.dropped)
    if not tracker.ok:
        return BugOutcome(f"{replies.NO_TRACKER_TOOL}\n\n{coverage.line(tracker)}")
    if run.status == "paused":
        return BugOutcome(replies.READ_NEEDS_APPROVAL)
    answer = run.parsed
    if answer is None:
        return BugOutcome(replies.STEP_LIMIT_NOTE if run.status in ("limit", "max_iterations") else replies.UNREADABLE_ANSWER)
    if answer.status == "off_topic":
        return BugOutcome(smalltalk.OFF_TOPIC_REPLY)
    if answer.status == "no_tracker_tool":
        return BugOutcome(replies.NO_TRACKER_TOOL)
    checked = checks.check_bugs(answer.bugs, turn.seen_text, deps.config, since=None if keys else w.since)
    if keys:
        return _by_key(checked, run.notes)
    return _sweep(checked, days, run.notes)


def _by_key(checked: checks.BugCheck, notes: tuple[str, ...]) -> BugOutcome:
    if len(checked.kept) == 1:
        return BugOutcome(bug=checked.kept[0], notes=notes)
    if len(checked.kept) > 1:
        return BugOutcome(choices=checked.kept, no_fix_version=checked.no_fix_version, notes=notes)
    lines = [f"{b.key} is {b.status or 'not in a done status'}, not done or released yet, so there is nothing to "
             "tell customers." for b in checked.not_done]
    lines += [f"{b.key} has no fix version, so I can't confirm it shipped." for b in checked.no_fix_version]
    return BugOutcome("\n\n".join([*lines, *notes]) if lines else "\n\n".join([replies.NOT_FOUND, *notes]))


def _sweep(checked: checks.BugCheck, days: int, notes: tuple[str, ...]) -> BugOutcome:
    if not checked.kept and not checked.no_fix_version:
        return BugOutcome("\n\n".join([replies.NOTHING_SHIPPED_IN_WINDOW.format(days=days), *notes]))
    return BugOutcome(choices=checked.kept, no_fix_version=checked.no_fix_version, window_days=days, notes=notes)
