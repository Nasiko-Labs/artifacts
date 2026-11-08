"""Draft step: one model call per candidate (4 at a time, inside the turn's draft window), then the code check,
then one batched repair call per 4 failing drafts, else the medoid reply's own text marked verbatim."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import grounding
import prompts
from llm import DRAFT_RESERVE_SECS, LlmError, TurnDeps, bounded, complete_json

REPAIR_BATCH = 4
DRAFT_MAX_TOKENS = 600


@dataclass(frozen=True)
class Draft:
    pattern: str
    text: str
    grounded: bool
    fallback: bool = False
    note: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @staticmethod
    def from_dict(data: dict) -> Draft:
        return Draft(str(data.get("pattern") or ""), str(data.get("text") or ""), bool(data.get("grounded")),
                     bool(data.get("fallback")), str(data.get("note") or ""))


@dataclass(frozen=True)
class Job:
    key: str
    sample: tuple[tuple[str, str], ...]     # (reply id, scrubbed text): the medoid first
    members: tuple[str, ...]                # scrubbed member texts, for the grounding check


def _valid_draft(data) -> dict | None:
    if not isinstance(data, dict):
        return None
    pattern, text = data.get("pattern"), data.get("draft")
    if not isinstance(text, str) or not text.strip():
        return None
    return {"pattern": pattern if isinstance(pattern, str) else "", "draft": text}


def fallback(job: Job, names: Sequence[str], why: str = "") -> Draft:
    rid, text = job.sample[0]
    body, _ = grounding.finalize(text, names)
    note = f"verbatim from reply {rid}" + (f"; {why}" if why else "")
    return Draft(grounding.fallback_pattern(text), body, grounded=True, fallback=True, note=note)


def _checked(job: Job, raw: dict, names: Sequence[str]) -> tuple[Draft, list[str]]:
    text, cut = grounding.finalize(raw["draft"], names)
    bad = grounding.ungrounded(text, job.members)
    pattern = raw.get("pattern", "")
    if not grounding.pattern_ok(pattern, job.members):
        pattern = grounding.fallback_pattern(job.sample[0][1])
    note = "cut to 120 words" if cut else ""
    return Draft(" ".join(pattern.split()), text, grounded=not bad, note=note), bad


async def _one(deps: TurnDeps, job: Job, context: Sequence[str], instruction: str = "", previous: str = "") -> dict:
    messages = prompts.draft_messages(job.sample, instruction, previous, context)
    return await complete_json(deps, messages, prompts.DRAFT_CONTRACT, _valid_draft, DRAFT_MAX_TOKENS)


async def _repair(deps: TurnDeps, failing: list[tuple[Job, Draft, list[str]]], context: Sequence[str],
                  names: Sequence[str]) -> dict[str, Draft]:
    fixed: dict[str, Draft] = {}
    for start in range(0, len(failing), REPAIR_BATCH):
        batch = failing[start:start + REPAIR_BATCH]
        ids = {f"g{n}": item for n, item in enumerate(batch, start=1)}
        messages = prompts.repair_drafts_messages([(gid, d.text, bad) for gid, (_, d, bad) in ids.items()], context)
        try:
            answer = await complete_json(deps, messages, prompts.REPAIR_CONTRACT,
                                         lambda d: d if isinstance(d, dict) and isinstance(d.get("drafts"), list)
                                         else None)
        except LlmError:
            continue
        for entry in answer["drafts"]:
            item = ids.get(str(entry.get("id"))) if isinstance(entry, dict) else None
            if item is None or not isinstance(entry.get("draft"), str):
                continue
            job, before, _ = item
            redone, bad = _checked(job, {"pattern": before.pattern, "draft": entry["draft"]}, names)
            if not bad:
                fixed[job.key] = redone
    return fixed


async def draft_all(deps: TurnDeps, jobs: Sequence[Job], names: Sequence[str],
                    context: Sequence[str]) -> dict[str, Draft]:
    window = deps.remaining() - DRAFT_RESERVE_SECS
    if window > 0:
        results, _ = await bounded([lambda job=job: _one(deps, job, context) for job in jobs], window)
    else:  # no time left for a model call: every candidate gets its medoid's own text
        results = [None] * len(jobs)
    drafts: dict[str, Draft] = {}
    failing: list[tuple[Job, Draft, list[str]]] = []
    for job, raw in zip(jobs, results, strict=True):
        if raw is None:
            drafts[job.key] = fallback(job, names, "the model gave no draft in time")
            continue
        checked, bad = _checked(job, raw, names)
        if bad:
            failing.append((job, checked, bad))
        else:
            drafts[job.key] = checked
    repaired = await _repair(deps, failing, context, names) if failing and deps.remaining() > 0 else {}
    for job, _, bad in failing:
        drafts[job.key] = repaired.get(job.key) or fallback(job, names, "the draft stated " + ", ".join(bad[:3])
                                                            + " that fewer than 2 replies say")
    return drafts


async def redraft(deps: TurnDeps, job: Job, previous: Draft, instruction: str, names: Sequence[str],
                  context: Sequence[str]) -> Draft | None:
    """One call for a changed draft ("shorter", "friendlier"); None when it fails the check."""
    try:
        raw = await _one(deps, job, context, instruction, previous.text)
    except LlmError:
        return None
    checked, bad = _checked(job, {"pattern": previous.pattern, "draft": raw["draft"]}, names)
    return None if bad else checked
