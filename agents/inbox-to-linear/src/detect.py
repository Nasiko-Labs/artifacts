"""The detect step: which threads are work requests (one tool-less LLM call per batch of 10), then the code
checks. A request needs an ask quote found verbatim in the email; the requester, link and ids come from
the thread, never from the model; priority and due date come from the email's own words, in code; an
issue-key link in the user's own reply marks it already tracked."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import replace

import issue
import prompts
from deps import TurnDeps
from llmcall import ask_once, load_json_object, text_of
from models import Request, Thread
from textcheck import appears_in, parse_day

log = logging.getLogger("inbox_to_linear.detect")

BATCH_SIZE = 10
MAX_BATCHES = 4
ASK_TYPES = ("bug", "feature", "task", "investigation", "review")
ISSUE_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
_MAX_TEXT_CHARS = 1500


async def detect(deps: TurnDeps, threads: Sequence[Thread], seen: str) -> tuple[tuple[Request, ...], int]:
    """(requests, dropped): `dropped` counts answers the code checks refused (no verbatim quote, unknown id)."""
    requests: tuple[Request, ...] = ()
    dropped = 0
    batches = [threads[i:i + BATCH_SIZE] for i in range(0, len(threads), BATCH_SIZE)][:MAX_BATCHES]
    for batch in batches:
        answer = load_json_object(await ask_once(deps, _messages(batch)))
        found, refused = checked(batch, answer, seen)
        requests, dropped = (*requests, *found), dropped + refused
    return await _repair_titles(deps, requests), dropped


def _messages(batch: Sequence[Thread]) -> tuple[dict, ...]:
    listing = json.dumps([{"id": t.id, "from": t.sender, "from_name": t.sender_name, "subject": t.subject,
                           "date": t.date, "text": t.text[:_MAX_TEXT_CHARS]} for t in batch],
                         ensure_ascii=False, indent=1)
    return ({"role": "system", "content": prompts.DETECT_SYSTEM},
            {"role": "user", "content": prompts.fence_emails(listing)})


def checked(batch: Sequence[Thread], answer: Mapping | None, seen: str) -> tuple[tuple[Request, ...], int]:
    by_id = {t.id: t for t in batch}
    raw = answer.get("results") if isinstance(answer, Mapping) else None
    found: tuple[Request, ...] = ()
    refused = 0
    for item in raw if isinstance(raw, list) else ():
        if not isinstance(item, Mapping) or item.get("is_request") is not True:
            continue
        thread = by_id.get(text_of(item.get("id")))
        quote = text_of(item.get("ask_quote"))
        if thread is None or not appears_in(quote, thread.text) or not appears_in(quote, seen):
            refused += 1
            continue
        found = (*found, to_request(thread, item, quote))
    return found, refused


def to_request(thread: Thread, item: Mapping, quote: str) -> Request:
    deadline = text_of(item.get("deadline_phrase"))
    deadline = deadline if appears_in(deadline, thread.text) or appears_in(deadline, thread.subject) else ""
    ask_type = text_of(item.get("ask_type")).casefold()
    tracked = ISSUE_KEY.search(thread.user_reply or "")
    return Request(
        id=thread.id, sender=thread.sender, sender_name=thread.sender_name, subject=thread.subject,
        ask_quote=quote, ask_type=ask_type if ask_type in ASK_TYPES else "task", title=text_of(item.get("title")),
        date=thread.date, link=thread.link, message_id=thread.message_id, thread_id=thread.thread_id,
        text=thread.text, deadline_phrase=deadline or None, due=issue.due_from(deadline, parse_day(thread.date)),
        priority=issue.priority_from(f"{thread.subject}\n{thread.text}"),
        tracked_key=tracked.group(0) if tracked else None, tracked_by="reply" if tracked else None,
        tracked_url=_url_with(thread.user_reply, tracked.group(0)) if tracked else None,
    )


def _url_with(text: str, key: str) -> str | None:
    return next((w.strip("<>()[],.") for w in text.split() if key in w and w.startswith(("http://", "https://"))),
                None)


async def _repair_titles(deps: TurnDeps, requests: tuple[Request, ...]) -> tuple[Request, ...]:
    """One repair call for every title that breaks the rules, then the code fallback."""
    bad = {r.id: r.title for r in requests if issue.title_problem(r.title)}
    if not bad:
        return requests
    answer = load_json_object(await ask_once(deps, (
        {"role": "system", "content": prompts.TITLE_REPAIR_SYSTEM},
        {"role": "user", "content": prompts.fence_emails(json.dumps(bad, ensure_ascii=False))})))
    fixed = answer if isinstance(answer, Mapping) else {}
    return tuple(_titled(r, text_of(fixed.get(r.id))) if r.id in bad else r for r in requests)


def _titled(request: Request, repaired: str) -> Request:
    title = repaired if repaired and not issue.title_problem(repaired) else issue.fix_title(
        repaired or request.title or request.subject, request.ask_type)
    return replace(request, title=title)
