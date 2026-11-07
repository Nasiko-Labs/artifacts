"""The state pass: one call over this paste's promises and the messages written after each of them.

`done` stands only when its evidence quote is verbatim in a message after the promise's own message;
otherwise it becomes `unclear` (as in the promise-tracker agent); `unclear` with no verbatim later quote is
`open`. No verdict, or a failed call: `open`.
Skipped when no promise has a later message."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace

import grounding as g
import llm
import prompts
from messages import Message
from promises import Promise, parse_states

log = logging.getLogger("promise-extractor.state")

LATER_MESSAGE_MAX_CHARS = 600
MAX_LATER_MESSAGES = 40


def later_than(promise: Promise, messages: Sequence[Message]) -> tuple[Message, ...]:
    ids = [m.id for m in messages]
    if promise.message_id not in ids:
        return ()
    return tuple(messages[ids.index(promise.message_id) + 1:])


async def apply_states(deps: llm.TurnDeps, promises: Sequence[Promise],
                       messages: Sequence[Message]) -> tuple[Promise, ...]:
    """`messages` is the whole paste (every speaker), in order."""
    candidates = [(f"p{n}", p) for n, p in enumerate(promises, start=1) if later_than(p, messages)]
    if not candidates or not deps.traceparent:
        return tuple(promises)
    first_index = min(_index(p, messages) for _, p in candidates)
    later = messages[first_index + 1:][:MAX_LATER_MESSAGES]
    listing = "\n".join(f'{pid} (in [{p.message_id}]): "{p.source_quote}"' for pid, p in candidates)
    block = "\n\n".join(f"[{m.id}]\n{m.text[:LATER_MESSAGE_MAX_CHARS]}" for m in later)
    try:
        parsed = await llm.complete_json(deps, prompts.state_messages(listing, block), prompts.STATE_CONTRACT,
                                         parse_states)
    except llm.LlmError:
        log.warning("state pass failed; promises stay open")
        return tuple(promises)
    verdicts = parsed["results"]
    by_promise = {id(p): verdicts.get(pid) for pid, p in candidates}
    return tuple(_judged(p, by_promise.get(id(p)), messages) for p in promises)


def _index(promise: Promise, messages: Sequence[Message]) -> int:
    return [m.id for m in messages].index(promise.message_id)


def _judged(promise: Promise, verdict: tuple[str, str] | None, messages: Sequence[Message]) -> Promise:
    if verdict is None:
        return promise
    state, evidence = verdict
    later = later_than(promise, messages)
    found = bool(evidence) and any(g.locate(evidence, g.normalise(m.text)) is not None for m in later)
    if state == "done":
        return replace(promise, state="done" if found else "unclear", evidence_quote=evidence if found else "")
    if state == "unclear" and not found:
        return replace(promise, state="open", evidence_quote="")  # nothing later points at it
    return replace(promise, state=state, evidence_quote=evidence if found else "")
