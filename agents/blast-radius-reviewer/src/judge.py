"""The judge map step: fired probes are packed into calls of at most 14,000 characters (at most 4 calls, run
concurrently); each returns one judgement per probe id. Probes that do not fit stay code-built items."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import excerpt
import llm
import prompts
from probes import PROBES

log = logging.getLogger("blast-radius-reviewer.judge")

MAX_PACKET_CHARS_PER_CALL = 14_000
MAX_JUDGE_CALLS = 4
TEXT_FIELDS = ("what", "why", "safe_if", "evidence_quote", "context_quote")
MAX_FIELD_CHARS = 600


@dataclass
class JudgeRun:
    judgements: dict[str, dict] = field(default_factory=dict)
    failed_calls: int = 0
    unjudged: list[str] = field(default_factory=list)


def packet(fired: dict, context: str) -> str:
    probe = PROBES[fired["probe"]]
    lines = [f"PROBE {fired['id']} ({probe.kind})", f"token: {fired['token']}", f"change: {fired['detail']}",
             f"question: {probe.question}", prompts.fence(fired["excerpt"], "INPUT")]
    found = excerpt.context_excerpts(context, fired["token"])
    if found:
        lines.append("context lines that mention the token:\n" + prompts.fence("\n...\n".join(found), "CONTEXT"))
    elif context:
        lines.append("context: none of the pasted context mentions this token.")
    return "\n".join(lines)


def groups(fired: Sequence[dict], context: str) -> tuple[list[list[tuple[str, str]]], list[str]]:
    """Packets grouped per call, in the given order, and the ids that did not fit in the call budget."""
    out: list[list[tuple[str, str]]] = []
    size, left = 0, []
    for item in fired:
        text = packet(item, context)
        if out and size + len(text) <= MAX_PACKET_CHARS_PER_CALL:
            out[-1].append((item["id"], text))
            size += len(text)
        elif len(out) < MAX_JUDGE_CALLS:
            out.append([(item["id"], text)])
            size = len(text)
        else:
            left.append(item["id"])
    return out, left


def validator(ids: set[str]):
    def validate(answer) -> dict | None:
        if not isinstance(answer, dict):
            return None
        if answer.get("status") == "off_topic":
            return {"status": "off_topic", "judgements": {}}
        rows = answer.get("judgements")
        if not isinstance(rows, list):
            return None
        found = {}
        for row in rows:
            clean = _row(row)
            if clean is not None and clean["probe_id"] in ids:
                found.setdefault(clean["probe_id"], clean)
        return {"status": "ok", "judgements": found} if found or not rows else None
    return validate


def _row(row) -> dict | None:
    if not isinstance(row, dict) or not isinstance(row.get("probe_id"), str):
        return None
    applies = row.get("applies")
    if isinstance(applies, str):
        applies = {"true": True, "false": False}.get(applies.strip().casefold(), "unclear")
    if applies not in (True, False, "unclear"):
        applies = "unclear"
    clean = {"probe_id": row["probe_id"].strip(), "applies": applies}
    for name in TEXT_FIELDS:
        value = row.get(name)
        clean[name] = value.strip()[:MAX_FIELD_CHARS] if isinstance(value, str) else ""
    return clean


async def run(deps: llm.TurnDeps, fired: Sequence[dict], context: str, intent: str,
              policy: Sequence[str]) -> JudgeRun:
    batches, left = groups(fired, context)
    result = JudgeRun(unjudged=list(left))

    async def one(batch: list[tuple[str, str]]) -> dict:
        ids = {pid for pid, _ in batch}
        messages = prompts.judge_messages([text for _, text in batch], intent, policy)
        return await llm.complete_json(deps, messages, prompts.JUDGE_SYSTEM, validator(ids))

    answers = await asyncio.gather(*(one(b) for b in batches), return_exceptions=True)
    for batch, answer in zip(batches, answers):
        if isinstance(answer, BaseException):
            log.warning("judge call failed: %s", answer)
            result.failed_calls += 1
            result.unjudged += [pid for pid, _ in batch]
            continue
        if answer["status"] == "off_topic":  # a diff was found in code, so this is in scope: leave unjudged
            result.unjudged += [pid for pid, _ in batch]
            continue
        result.judgements.update(answer["judgements"])
        result.unjudged += [pid for pid, _ in batch if pid not in answer["judgements"]]
    return result
