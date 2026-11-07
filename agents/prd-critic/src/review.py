"""One review: sectionize (code, model fallback) -> classify requirements (1-3 calls) -> rubric (code) ->
reviewer questions (1 call) -> envelope. At most 6 model calls; optional calls are skipped near the turn
deadline, and with no time left for the model the result is the rubric alone, marked low confidence."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import envelope as envelope_mod
import prompts
import questions as questions_mod
import requirements as reqs_mod
import rubric
from inputs import Options, word_count
from llm import OPTIONAL_CALL_RESERVE_SECS, LlmError, TurnDeps, complete_json
from metrics import metric_lines
from sections import (
    LABELS,
    REQUIRED,
    Layout,
    found_by_heading,
    layout,
    number_lines,
    numbered_block,
    with_ranges,
)

log = logging.getLogger("prd-critic.review")

MIN_HEADED_SECTIONS = 3
MAX_BATCHES = 3
OFF_TOPIC_MAX_HEADED = 1    # a model's off-topic verdict counts only when headings found at most this many sections
CLASSIFY_MAX_TOKENS = 3_000


@dataclass
class Trace:
    """What happened to the optional model steps this turn; feeds confidence and the notes."""

    located: bool = False
    locate_failed: bool = False
    batch_failures: int = 0
    unclassified: int = 0
    questions_failed: bool = False
    rubric_only: bool = False
    off_topic: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Review:
    status: str                  # ok | off_topic | failed
    envelope: dict | None = None


def _status(data: Any, key: str, kind: type) -> dict | None:
    if not isinstance(data, dict):
        return None
    if data.get("status") == "off_topic":
        return {"status": "off_topic", key: kind()}
    value = data.get(key)
    return {"status": "ok", key: value} if isinstance(value, kind) else None


async def run(deps: TurnDeps, prd: str, opts: Options, policy: Sequence[str] = (), one_block: bool = False) -> Review:
    lines = number_lines(prd)
    lay, trace = layout(lines), Trace()
    headed = found_by_heading(lay)
    if one_block or headed < MIN_HEADED_SECTIONS:
        lay = await _locate(deps, lay, policy, trace)
        if one_block and trace.locate_failed:
            return Review("failed")
    reqs = reqs_mod.split(lay.body("requirements"))
    flags = await _classify(deps, reqs, policy, trace)
    if trace.off_topic and headed <= OFF_TOPIC_MAX_HEADED:
        return Review("off_topic")
    own = questions_mod.open_questions(lay)
    inputs = rubric.Inputs(lay, tuple(reqs), flags, tuple(metric_lines(lay.body("metrics"))), own, word_count(prd),
                           opts.lenient)
    scored = rubric.evaluate(inputs)
    asked = await _questions(deps, lines, own, scored["gaps"], policy, trace)
    if trace.off_topic and headed <= OFF_TOPIC_MAX_HEADED:
        return Review("off_topic")
    return Review("ok", envelope_mod.build(scored, lay, reqs, own, asked, trace, opts))


async def _locate(deps: TurnDeps, lay: Layout, policy: Sequence[str], trace: Trace) -> Layout:
    missing = [k for k in REQUIRED if k not in lay.sections]
    if not missing:
        return lay
    if not deps.has_time(OPTIONAL_CALL_RESERVE_SECS):
        trace.locate_failed = True
        return lay
    names = [LABELS[k] for k in missing]
    try:
        data = await complete_json(deps, prompts.locate_messages(numbered_block(lay.lines), names, policy),
                                   prompts.LOCATE_CONTRACT, lambda d: _status(d, "sections", dict))
    except LlmError as e:
        log.warning("locate failed: %s", e)
        trace.locate_failed = True
        return lay
    if data["status"] == "off_topic":
        trace.off_topic = True
        return lay
    trace.located = True
    by_label = {LABELS[k].casefold(): k for k in REQUIRED}
    ranges: dict[str, list[tuple[int, int]]] = {}
    for name, spans in data["sections"].items():
        key = by_label.get(str(name).casefold()) or (name if name in LABELS else None)
        if key in missing and isinstance(spans, list):
            ranges[key] = [(int(s[0]), int(s[1])) for s in spans
                           if isinstance(s, list | tuple) and len(s) == 2 and all(isinstance(v, int) for v in s)]
    return with_ranges(lay, ranges)


async def _classify(deps: TurnDeps, reqs: list, policy: Sequence[str], trace: Trace) -> dict:
    groups = reqs_mod.batches(reqs)
    trace.unclassified = sum(len(g) for g in groups[MAX_BATCHES:])
    groups = groups[:MAX_BATCHES]
    if groups and not deps.has_time(OPTIONAL_CALL_RESERVE_SECS):
        trace.rubric_only = True
        return {}
    results = await asyncio.gather(*(_classify_one(deps, g, policy) for g in groups))
    flags: dict = {}
    for result in results:
        if result is None:
            trace.batch_failures += 1
        elif result == "off_topic":
            trace.off_topic = True
        else:
            flags.update(result)
    if groups and trace.batch_failures == len(groups):
        trace.rubric_only = True
    return flags


async def _classify_one(deps: TurnDeps, group: list, policy: Sequence[str]) -> dict | str | None:
    items = "\n".join(f"{r.id}: {r.text}" for r in group)
    try:
        data = await complete_json(deps, prompts.classify_messages(items, policy), prompts.CLASSIFY_CONTRACT,
                                   lambda d: _status(d, "requirements", list), CLASSIFY_MAX_TOKENS)
    except LlmError as e:
        log.warning("classify batch failed: %s", e)
        return None
    return "off_topic" if data["status"] == "off_topic" else reqs_mod.parse_flags(data, group)


async def _questions(deps: TurnDeps, lines: tuple[str, ...], own: Sequence[tuple[int, str]], gaps: list[dict],
                     policy: Sequence[str], trace: Trace) -> list[dict]:
    if trace.rubric_only or not deps.has_time():
        trace.rubric_only = True
        return []
    found = json.dumps([g["issue"] for g in gaps[:30]], ensure_ascii=False)
    try:
        data = await complete_json(deps, prompts.questions_messages(numbered_block(lines), found, policy),
                                   prompts.QUESTIONS_CONTRACT, lambda d: _status(d, "questions", list))
    except LlmError as e:
        log.warning("questions failed: %s", e)
        trace.questions_failed = True
        return []
    if data["status"] == "off_topic":
        trace.off_topic = True
        return []
    return questions_mod.check(data, lines, own, gaps)


async def classify_scope(deps: TurnDeps, text: str, policy: Sequence[str] = ()) -> str:
    """A short message with no PRD: off_topic, or needs_prd. A failed call counts as needs_prd (asking for
    the PRD never answers an off-topic question)."""
    def validate(data: Any) -> dict | None:
        ok = isinstance(data, dict) and data.get("status") in ("off_topic", "needs_prd")
        return data if ok else None
    try:
        data = await complete_json(deps, prompts.scope_messages(text, policy), prompts.SCOPE_CONTRACT, validate)
    except LlmError as e:
        log.warning("scope check failed: %s", e)
        return "needs_prd"
    return data["status"]
