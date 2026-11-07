"""The judge step: did each thread reach a conclusion that implies work nobody has done (one tool-less LLM call
per batch of 5 threads, at most 8 batches), then the code gates. A finding needs an action quote found
verbatim in the thread and in this turn's input, sitting in the last third of the thread; a later message
reporting completion drops it whatever the model said; the owner hint survives only when its name is a
participant and its quote is theirs; the channel, link, participants and dates come from the thread."""

from __future__ import annotations

import json
import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import issue
import prompts
from deps import TurnDeps
from llmcall import ask_once, load_json_object, text_of
from models import Finding, ThreadCandidate
from textcheck import appears_in

log = logging.getLogger("thread_to_issue.judge")

BATCH_SIZE = 5
MAX_BATCHES = 8
KINDS = ("bug", "task", "docs", "follow-up")
TIME_RESERVE_SECS = 90.0
_DONE = re.compile(r"\b(?:fixed|merged|done|shipped|deployed|resolved|landed|released|completed)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Judged:
    findings: tuple[Finding, ...] = ()
    refused: int = 0          # answers the code refused (quote not verbatim, unknown id, not in the last third)
    done: int = 0             # concluded but already done
    timed_out: bool = False


async def judge(deps: TurnDeps, threads: Sequence[ThreadCandidate], seen: str) -> Judged:
    result = Judged()
    batches = [threads[i:i + BATCH_SIZE] for i in range(0, len(threads), BATCH_SIZE)][:MAX_BATCHES]
    for batch in batches:
        if deps.out_of_time(TIME_RESERVE_SECS):
            log.warning("turn deadline near: %d thread batch(es) left unjudged", len(batches))
            result = replace(result, timed_out=True)
            break
        answer = load_json_object(await ask_once(deps, _messages(batch)))
        found = checked(batch, answer, seen)
        result = Judged((*result.findings, *found.findings), result.refused + found.refused,
                        result.done + found.done, result.timed_out)
    return replace(result, findings=await _repair_titles(deps, result.findings))


def last_third_start(count: int) -> int:
    """Index (0-based) of the first message in the last third of a thread of `count` messages."""
    return count - math.ceil(count / 3)


def _messages(batch: Sequence[ThreadCandidate]) -> tuple[dict, ...]:
    listing = json.dumps([{"id": t.id, "channel": t.channel, "last_third_starts_at_n": last_third_start(
        len(t.messages)) + 1, "messages": [{"n": n, "author": m.author, "text": m.text}
                                             for n, m in enumerate(t.messages, start=1)]} for t in batch],
                         ensure_ascii=False, indent=1)
    return ({"role": "system", "content": prompts.JUDGE_SYSTEM},
            {"role": "user", "content": prompts.fence_threads(listing)})


def checked(batch: Sequence[ThreadCandidate], answer: Mapping | None, seen: str) -> Judged:
    by_id = {t.id: t for t in batch}
    raw = answer.get("results") if isinstance(answer, Mapping) else None
    found: tuple[Finding, ...] = ()
    refused = done = 0
    for item in raw if isinstance(raw, list) else ():
        if not isinstance(item, Mapping) or item.get("concluded") is not True:
            continue
        thread = by_id.get(text_of(item.get("id")))
        quote = text_of(item.get("action_quote"))
        at = _quote_index(thread, quote) if thread is not None else None
        if thread is None or at is None or not appears_in(quote, seen):
            refused += 1
            continue
        if item.get("already_done") is True or _done_after(thread, at):
            done += 1
            continue
        if at < last_third_start(len(thread.messages)):
            refused += 1
            continue
        found = (*found, to_finding(thread, item, quote))
    return Judged(found, refused, done)


def _quote_index(thread: ThreadCandidate, quote: str) -> int | None:
    """The last message that contains the quote verbatim, or None."""
    hits = [i for i, m in enumerate(thread.messages) if appears_in(quote, m.text)]
    return hits[-1] if hits else None


def _done_after(thread: ThreadCandidate, index: int) -> bool:
    return any(_DONE.search(m.text) for m in thread.messages[index + 1:])


def to_finding(thread: ThreadCandidate, item: Mapping, quote: str) -> Finding:
    kind = text_of(item.get("kind")).casefold()
    owner, owner_quote = _owner(thread, item.get("owner_hint"))
    return Finding(
        id=thread.id, channel=thread.channel, action=text_of(item.get("action")) or quote, action_quote=quote,
        kind=kind if kind in KINDS else "task", title=text_of(item.get("title")), channel_id=thread.channel_id,
        thread_ts=thread.thread_ts, link=thread.link, date=thread.date, last_ts=thread.last_ts, text=thread.text,
        participants=thread.participants, owner_name=owner, owner_quote=owner_quote,
        due=issue.due_from_quote(quote, thread.date), pasted=thread.pasted)


def _owner(thread: ThreadCandidate, hint: object) -> tuple[str | None, str | None]:
    if not isinstance(hint, Mapping):
        return None, None
    name, quote = text_of(hint.get("name")), text_of(hint.get("quote"))
    author = next((p for p in thread.participants if p.casefold() == name.casefold()), None)
    if author is None or not any(m.author == author and appears_in(quote, m.text) for m in thread.messages):
        return None, None
    return author, quote


async def _repair_titles(deps: TurnDeps, findings: tuple[Finding, ...]) -> tuple[Finding, ...]:
    """One repair call for every title that breaks the rules, then the code fallback."""
    bad = {f.id: f.title or f.action for f in findings if issue.title_problem(f.title)}
    if not bad:
        return findings
    answer = load_json_object(await ask_once(deps, (
        {"role": "system", "content": prompts.TITLE_REPAIR_SYSTEM},
        {"role": "user", "content": prompts.fence_threads(json.dumps(bad, ensure_ascii=False))})))
    fixed = answer if isinstance(answer, Mapping) else {}
    return tuple(_titled(f, text_of(fixed.get(f.id))) if f.id in bad else f for f in findings)


def _titled(finding: Finding, repaired: str) -> Finding:
    title = repaired if repaired and not issue.title_problem(repaired) else issue.fix_title(
        repaired or finding.title or finding.action, finding.kind)
    return replace(finding, title=title)
