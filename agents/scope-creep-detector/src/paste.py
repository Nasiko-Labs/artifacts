"""The paste fallback: a pasted issue list (no tracker needed). One tool-less LLM call transcribes it into
the same records, every value checked against the paste itself; the method and rendering are the same
code as the connected path. A pasted `added_at` becomes an `added_to_sprint` event, `removed_at` a removal."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import timedelta

import prompts
from checks import Seen, check_issues, items, parse_answer
from deps import TurnDeps
from records import HistoryEvent, Snapshot, Sprint
from steps import ask_once

log = logging.getLogger("scope_creep.paste")

DEFAULT_SPRINT_DAYS = 14


async def read_paste(deps: TurnDeps, text: str) -> tuple[Snapshot | None, str]:
    """(snapshot, "") or (None, "off_topic" | "unreadable")."""
    answer = parse_answer(await ask_once(deps, (
        {"role": "system", "content": prompts.paste_system(deps.now)},
        {"role": "user", "content": prompts.fence_paste(text)},
    )))
    if answer is None:
        return None, "unreadable"
    if answer.get("status") == "off_topic":
        return None, "off_topic"
    seen = Seen(text)
    sprint = _sprint(answer.get("sprint"), seen)
    if sprint is None:
        return None, "unreadable"
    raw_issues = [{**i, "url": i.get("url") or "", "in_sprint_now": not i.get("removed_at")}
                  for i in items(answer, "issues")]
    issues, _ = check_issues({"issues": [_with_key_url(i, seen) for i in raw_issues]}, seen)
    if not issues:
        return None, "unreadable"
    keys = {i.key for i in issues}
    events = tuple(e for raw in raw_issues if raw.get("key") in keys for e in _events(raw, seen))
    log.info("paste: %d issues, %d events", len(issues), len(events))
    with_dates = frozenset(e.issue_key for e in events)
    return Snapshot(sprint=sprint, issues=issues, events=events, history_keys=with_dates), ""


def _sprint(raw: object, seen: Seen) -> Sprint | None:
    if not isinstance(raw, Mapping):
        return None
    starts = seen.has_time(raw.get("starts_at"))
    if starts is None:
        return None
    ends = seen.has_time(raw.get("ends_at")) or starts + timedelta(days=DEFAULT_SPRINT_DAYS)
    name = raw.get("name") if seen.has(raw.get("name")) else "the pasted sprint"
    return Sprint(id=str(raw.get("id") or name), name=str(name).strip(), starts_at=starts, ends_at=ends)


def _with_key_url(raw: Mapping, seen: Seen) -> dict:
    """A pasted row may have no URL; the key stands in for it (rendered as plain text, not a link)."""
    url = raw.get("url") if seen.has(raw.get("url")) else raw.get("key")
    return {**raw, "url": url}


def _events(raw: Mapping, seen: Seen) -> tuple[HistoryEvent, ...]:
    out = []
    for at_key, who_key, kind in (("added_at", "added_by", "added_to_sprint"),
                                  ("removed_at", "removed_by", "removed_from_sprint")):
        at = seen.has_time(raw.get(at_key))
        if at is not None:
            who = raw.get(who_key)
            out.append(HistoryEvent(issue_key=str(raw["key"]).strip(), at=at, kind=kind,
                                    actor=who.strip() if seen.has(who) else ""))
    return tuple(out)
