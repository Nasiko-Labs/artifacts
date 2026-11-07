"""One pre-mortem run: 4 voices (concurrent) -> check (code) -> [one field repair] -> [one refill] ->
consolidate (1 call) -> rank (code). At most 7 model calls; every optional call is skipped when the turn
deadline is near."""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace

import prompts
from consolidate import Consolidated, consolidate
from grounding import normalise
from llm import OPTIONAL_CALL_RESERVE_SECS, LlmError, TurnDeps, call, load
from plan import numbered_block
from rank import LENS_ORDER, MIN_COUNT, Story, rank
from specific import OK, REPAIR, Candidate, assess, dedup
from voices import FAILED, OFF_TOPIC, refill, run_voices

log = logging.getLogger("pre-mortem.pipeline")


@dataclass(frozen=True)
class Outcome:
    status: str                              # ok | off_topic | failed
    stories: tuple[Story, ...] = ()          # ranked, top N
    assumptions: tuple[dict, ...] = ()
    questions: tuple[str, ...] = ()
    survivors: tuple[Candidate, ...] = ()    # every checked candidate, kept for "add context"
    left_out: int = 0
    voice_failures: int = 0
    consolidate_ok: bool = True


def ground_of(lines: Sequence[str], context: Sequence[str]) -> str:
    return normalise("\n".join([*lines, *context]))


async def run(deps: TurnDeps, lines: tuple[str, ...], context: tuple[str, ...], policy: Sequence[str] = (),
              count: int = 7, focus: str = "") -> Outcome:
    numbered, ground = numbered_block(lines), ground_of(lines, context)
    results = await run_voices(deps, numbered, context, policy)
    statuses = Counter(r.status for r in results)
    answered = len(results) - statuses[FAILED]
    if answered == 0:
        return Outcome("failed", voice_failures=statuses[FAILED])
    if statuses[OFF_TOPIC] * 2 > answered:
        return Outcome("off_topic")
    candidates = [c for r in results for c in r.candidates]
    survivors = dedup(await _screen(deps, candidates, lines, ground))
    checked = len(candidates)
    if len(survivors) < MIN_COUNT and deps.has_time(OPTIONAL_CALL_RESERVE_SECS):
        extra = await _refill(deps, survivors, numbered, context, policy, candidates)
        checked += len(extra)
        survivors = dedup([*survivors, *await _screen(deps, extra, lines, ground)])
    merged = await consolidate(deps, survivors, lines, numbered, context, ground, policy, focus)
    return _outcome(merged, survivors, count, focus, checked, statuses[FAILED])


def _outcome(merged: Consolidated, survivors: list[Candidate], count: int, focus: str, checked: int,
             failures: int) -> Outcome:
    ranked = rank(merged.stories, count, focus)
    return Outcome("ok", tuple(ranked), merged.assumptions, merged.questions, tuple(survivors),
                   left_out=max(0, checked - len(survivors)), voice_failures=failures,
                   consolidate_ok=merged.ok)


async def rerank(deps: TurnDeps, survivors: Sequence[Candidate], lines: tuple[str, ...], context: tuple[str, ...],
                 policy: Sequence[str], count: int, focus: str, left_out: int, failures: int) -> Outcome:
    """"add context": the stored survivors re-checked against the new grounding, consolidated again."""
    ground = ground_of(lines, context)
    kept = [c for c in survivors if assess(c, lines, ground)[0] == OK]
    merged = await consolidate(deps, kept, lines, numbered_block(lines), context, ground, policy, focus)
    return _outcome(merged, kept, count, focus, len(kept) + left_out, failures)


async def _screen(deps: TurnDeps, candidates: Sequence[Candidate], lines: tuple[str, ...], ground: str) -> list[Candidate]:
    kept: list[Candidate] = []
    fixable: list[Candidate] = []
    for candidate in candidates:
        verdict, located = assess(candidate, lines, ground)
        if verdict == OK:
            kept.append(located)
        elif verdict == REPAIR:
            fixable.append(located)
        else:
            log.info("dropped %s: %s", candidate.id, verdict)
    if fixable:
        kept += await _repair_fields(deps, fixable, lines, ground)
    return kept


async def _repair_fields(deps: TurnDeps, items: list[Candidate], lines: tuple[str, ...], ground: str) -> list[Candidate]:
    """One call rewrites filler early warnings / mitigations; whatever is still filler is dropped."""
    if not deps.has_time(OPTIONAL_CALL_RESERVE_SECS) or not deps.budget.take_repair():
        return []
    block = json.dumps([{"id": c.id, "story": c.story, "plan_ref": c.plan_ref, "early_warning": c.early_warning,
                         "mitigation": c.mitigation} for c in items], ensure_ascii=False)
    try:
        data = load(await call(deps, prompts.repair_fields_messages(block)))
    except LlmError as e:
        log.warning("field repair failed: %s", e)
        return []
    fixes = data.get("fixes") if isinstance(data, dict) else None
    by_id = {f.get("id"): f for f in fixes or () if isinstance(f, dict)}
    kept: list[Candidate] = []
    for c in items:
        fix = by_id.get(c.id) or {}
        fixed = replace(c, early_warning=" ".join(str(fix.get("early_warning") or "").split()),
                        mitigation=" ".join(str(fix.get("mitigation") or "").split()))
        verdict, located = assess(fixed, lines, ground)
        if verdict == OK:
            kept.append(located)
    return kept


async def _refill(deps: TurnDeps, survivors: list[Candidate], numbered: str, context: tuple[str, ...],
                  policy: Sequence[str], candidates: list[Candidate]) -> tuple[Candidate, ...]:
    """One call to the lens with the fewest survivors (a failed lens counts as zero)."""
    per_lens = Counter(c.lens for c in survivors)
    lens = min(LENS_ORDER, key=lambda name: (per_lens[name], LENS_ORDER.index(name)))
    start = sum(1 for c in candidates if c.lens == lens)
    result = await refill(deps, lens, numbered, context, [c.line for c in survivors], start, policy)
    return result.candidates
