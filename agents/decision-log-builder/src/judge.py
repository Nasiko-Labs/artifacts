"""The judge step: one tool-less LLM call per channel that has a decision signal (chosen in code), whose
JSON is checked in code (verify.py). Channels are judged one after another, most signals first, at most
MAX_JUDGE_CALLS of them and only while the turn has time left; the rest get a coverage note."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

import prompts
import verify
from deps import TurnDeps
from llmcall import ask_once, load_json_object
from models import Decision, Message

log = logging.getLogger("decision_log.judge")

MAX_JUDGE_CALLS = 4
MAX_MESSAGES_PER_CALL = 60
JUDGE_RESERVE_SECS = 120.0  # left for the document steps
_SIGNAL = re.compile("|".join(re.escape(s) for s in prompts.SIGNALS) + r"|\+1|✅|:white_check_mark:", re.IGNORECASE)


@dataclass(frozen=True)
class Judged:
    decisions: tuple[Decision, ...]
    dropped: int = 0
    not_decided: int = 0
    notes: tuple[str, ...] = ()


def has_signal(message: Message) -> bool:
    return bool(_SIGNAL.search(message.text))


def channels_to_judge(messages: Sequence[Message]) -> tuple[tuple[Message, ...], ...]:
    """Messages grouped by channel; only channels with a decision signal, most signals first."""
    groups: dict[str, tuple[Message, ...]] = {}
    for m in messages:
        groups = {**groups, m.where: (*groups.get(m.where, ()), m)}
    signalled = [g for g in groups.values() if any(has_signal(m) for m in g)]
    return tuple(sorted(signalled, key=lambda g: -sum(1 for m in g if has_signal(m))))


async def judge(deps: TurnDeps, messages: Sequence[Message], evidence: str) -> Judged:
    groups = channels_to_judge(messages)
    chosen: tuple[tuple[Message, ...], ...] = ()
    candidates: tuple[verify.Candidate, ...] = ()
    dropped = not_decided = offset = 0
    for group in groups[:MAX_JUDGE_CALLS]:
        if deps.out_of_time(reserve=JUDGE_RESERVE_SECS):
            break
        chosen = (*chosen, group)
        raw = await _judge_channel(deps, group)
        checked = verify.verify(raw, group, evidence, start=offset)
        offset += len(raw)
        candidates = (*candidates, *checked.candidates)
        dropped += checked.dropped
        not_decided += checked.not_decided
    skipped = groups[len(chosen):]
    names = ", ".join("#" + g[0].where for g in skipped)
    note = (f"Chat: {len(skipped)} more channel(s) with decision signals were not checked this turn ({names}); "
            "name them to check them.")
    notes = (note,) if skipped else ()
    log.info("judged %d channel(s): %d candidate(s), %d dropped, %d not decided", len(chosen), len(candidates),
             dropped, not_decided)
    return Judged(verify.finalize(candidates), dropped, not_decided, notes)


async def _judge_channel(deps: TurnDeps, group: Sequence[Message]) -> list[dict]:
    lines = "\n".join(_line(m) for m in group[:MAX_MESSAGES_PER_CALL])
    messages = ({"role": "system", "content": prompts.JUDGE_SYSTEM},
                {"role": "user", "content": f"Channel #{group[0].where}:\n{prompts.fence_messages(lines)}"})
    data = load_json_object(await ask_once(deps, messages))
    raw = data.get("decisions") if isinstance(data, dict) else None
    return [d for d in raw if isinstance(d, dict)] if isinstance(raw, list) else []


def _line(m: Message) -> str:
    text = " ".join(m.text.split())
    return f"{m.id} | {m.day} | {m.who} | thread {m.thread or '-'} | {text}"
