"""The model step for the threads no rule decided: batches of 12, JSON out, every answer checked in code.

Guards (plan section 9): an urgent call without a quote found in the thread is demoted to reply; a reply
whose newest message holds no question or request is demoted to ignore; a bulk sender is never lifted
above ignore; a reason carrying a number or link the thread does not contain is replaced; an unknown id
is dropped. A thread the model skipped is sorted from its signals and counted."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import prompts
from deps import LLM_CALL_TIMEOUT_SECS, TurnDeps
from models import BUCKETS, Mail, Thread
from rules import make_thread
from signals import Signals, own_text
from tool_loop import run_tool_loop

log = logging.getLogger("inbox_triage.classify")

BATCH_SIZE = 12
MAX_BATCHES = 5
MAX_THREAD_CHARS = 1500
MAX_REASON_CHARS = 140
_SPACES = re.compile(r"\s+")
_URL = re.compile(r"https?://\S+")
_NUMBER = re.compile(r"\d[\d.,:/-]*")
_MARKUP = re.compile(r"[*_`#<>|\[\]]")
# "Something is broken or blocked and the thread says so" (plan section 9): words an urgent quote may carry
# when no deadline today backs it.
BROKEN = re.compile(r"\b(?:broken|blocked|blocking|blocker|down|outage|failing|failed|failure|error|incident|"
                    r"stuck|can't access|cannot access|unable to|not working|crash\w*)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Classified:
    threads: tuple[Thread, ...]
    demoted: int = 0
    dropped: int = 0
    unclassified: int = 0
    llm_calls: int = 0
    out_of_time: bool = False


class _NoTools:
    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]], **kwargs: Any) -> str:
    """One tool-less LLM call through the kit loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), _NoTools(), deps.traceparent, max_iterations=1,
                                 timeout=LLM_CALL_TIMEOUT_SECS, **kwargs)
    return result.text.strip()


async def classify(deps: TurnDeps, items: Sequence[tuple[Mail, Signals]]) -> Classified:
    out: list[Thread] = []
    totals = {"demoted": 0, "dropped": 0, "unclassified": 0, "calls": 0}
    late = False
    batches = [items[i:i + BATCH_SIZE] for i in range(0, len(items), BATCH_SIZE)]
    for index, batch in enumerate(batches):
        if index >= MAX_BATCHES or deps.out_of_time():
            late = late or deps.out_of_time()
            out.extend(_fallback(m, s) for m, s in batch)
            totals["unclassified"] += len(batch)
            continue
        answer = await ask_once(deps, ({"role": "system", "content": prompts.CLASSIFY_SYSTEM},
                                       {"role": "user", "content": prompts.classify_user(_blocks(batch, deps),
                                                                                          deps.today.isoformat())}),
                                response_format={"type": "json_object"})
        totals["calls"] += 1
        threads, counts = check_batch(batch, _results(answer), deps.today)
        out.extend(threads)
        for key, value in counts.items():
            totals[key] += value
    return Classified(tuple(out), totals["demoted"], totals["dropped"], totals["unclassified"], totals["calls"], late)


def _blocks(batch: Sequence[tuple[Mail, Signals]], deps: TurnDeps) -> str:
    return "\n\n".join(_block(m, s, deps.today) for m, s in batch)


def _block(mail: Mail, sig: Signals, today: date) -> str:
    deadline = "none stated"
    if sig.deadline:
        when = "today or overdue" if sig.deadline.date <= today else "not yet due"
        deadline = f"{sig.deadline.date.isoformat()}, {when} (\"{sig.deadline.phrase}\")"
    age = "unknown" if sig.age_hours is None else f"{sig.age_hours} hours ago"
    body = own_text(mail.last_text)[:MAX_THREAD_CHARS]
    return (f"[id: {mail.id}] from: {mail.sender} | sent to you directly: {'yes' if sig.direct_to_me else 'no'} | "
            f"VIP: {'yes' if sig.from_vip else 'no'} | received: {age} | deadline: {deadline} | "
            f"messages in thread: {mail.msg_count}\nsubject: {mail.subject}\nnewest message:\n{body}")


def _results(answer: str) -> list[Mapping[str, Any]]:
    text = answer.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        data = json.loads(text)
    except ValueError:
        log.info("classifier answer was not JSON")
        return []
    results = data.get("results") if isinstance(data, Mapping) else data
    return [r for r in results if isinstance(r, Mapping)] if isinstance(results, list) else []


def check_batch(batch: Sequence[tuple[Mail, Signals]], results: Sequence[Mapping[str, Any]],
                today: date) -> tuple[list[Thread], dict[str, int]]:
    """The batch's threads, each from the model's checked answer or, when it gave none, from its signals."""
    by_id = {m.id: (m, s) for m, s in batch}
    counts = {"demoted": 0, "dropped": 0, "unclassified": 0}
    decided: dict[str, Thread] = {}
    for raw in results:
        tid = str(raw.get("id") or "")
        if tid not in by_id or tid in decided or raw.get("bucket") not in BUCKETS:
            counts["dropped"] += 1
            continue
        thread, demoted, dropped = verify(raw, *by_id[tid], today)
        decided[tid] = thread
        counts["demoted"] += demoted
        counts["dropped"] += dropped
    threads = []
    for mail, sig in batch:
        if mail.id in decided:
            threads.append(decided[mail.id])
        else:
            counts["unclassified"] += 1
            threads.append(_fallback(mail, sig))
    return threads, counts


def verify(raw: Mapping[str, Any], mail: Mail, sig: Signals, today: date) -> tuple[Thread, int, int]:
    """(thread, demoted, dropped) for one model answer."""
    bucket = raw["bucket"]
    evidence = _verbatim(raw.get("evidence"), mail.text)
    dropped = int(bool(raw.get("evidence")) and evidence is None)
    reason, reason_dropped = _reason(raw.get("reason"), mail.text, bucket)
    demoted = 0
    if sig.is_bulk and not sig.from_vip and bucket != "ignore":
        bucket, reason, demoted = "ignore", f"bulk sender ({sig.bulk_why}); kept in ignore", 1
    if bucket == "urgent" and evidence is None:
        bucket, demoted = "reply", 1
        reason = f"{reason} (urgent not backed by a quote)"
    elif bucket == "urgent" and not (_due(sig, today) or BROKEN.search(evidence)):
        bucket, demoted = "reply", 1
        reason = f"{reason} (no deadline today and nothing broken in the quote)"
    if bucket == "reply" and sig.question_quote is None:
        bucket, demoted = "ignore", 1
        reason = f"{reason} (no question or request found)"
    thread = make_thread(mail, sig, bucket, reason, evidence, "model")
    return thread, demoted, dropped + reason_dropped


def _due(sig: Signals, today: date) -> bool:
    return sig.deadline is not None and sig.deadline.date <= today


def _fallback(mail: Mail, sig: Signals) -> Thread:
    if sig.question_quote:
        return make_thread(mail, sig, "reply", "sorted from signals: asks you something", sig.question_quote,
                           "fallback")
    return make_thread(mail, sig, "ignore", "sorted from signals: no ask found", None, "fallback")


def normalize(text: str) -> str:
    return _SPACES.sub(" ", (text or "").casefold()).strip().strip("\"'“”")


def _verbatim(evidence: Any, text: str) -> str | None:
    if not isinstance(evidence, str) or not evidence.strip():
        return None
    quote = evidence.strip().strip("\"'“”").strip()
    return quote if quote and normalize(quote) in normalize(text) else None


def _reason(raw: Any, text: str, bucket: str) -> tuple[str, int]:
    default = {"urgent": "needs you today", "reply": "waiting on your answer", "ignore": "no action needed"}[bucket]
    if not isinstance(raw, str) or not raw.strip():
        return default, 0
    reason = _MARKUP.sub("", _SPACES.sub(" ", raw)).strip()[:MAX_REASON_CHARS]
    haystack = normalize(text)
    unbacked = [t for t in (*_URL.findall(reason), *_NUMBER.findall(reason)) if normalize(t) not in haystack]
    if unbacked:
        return default, 1
    return reason or default, 0

