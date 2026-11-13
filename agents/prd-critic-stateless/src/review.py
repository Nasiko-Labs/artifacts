"""One review: sectionize (code) -> one crew kickoff -> rubric (code) -> envelope.

The crew is [locator (only when headings found fewer than 3 sections)] -> PRD critic -> skeptical review lead:
2 model calls for a headed PRD, 3 for an unheaded one. The critic flags each requirement and writes reviewer
questions; the review lead drops the weak ones; code applies only valid drops to known ids, then scores. With no
time left for the model, or when the crew fails, the result is the rubric alone, marked low confidence. An unusable
answer gets the turn's one repair call (`repair.kickoff_checked`); a critic answer still unusable after it gives
the rubric alone with a note saying so, and a one-block PRD whose sections still can't be located is `unusable`."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import critique
import envelope as envelope_mod
import prompts
import questions as questions_mod
import requirements as reqs_mod
import rubric
from inputs import Options, word_count
from llm import LlmError, TurnDeps, UnusableAnswer
from metrics import metric_lines
from repair import Check, kickoff_checked
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
OFF_TOPIC_MAX_HEADED = 1    # a model's off-topic verdict counts only when headings found at most this many sections
MAX_GAPS_SHOWN = 30


@dataclass
class Trace:
    """What happened to the model steps this turn; feeds confidence and the notes."""

    located: bool = False
    locate_failed: bool = False
    unclassified: int = 0
    review_failed: bool = False
    rubric_only: bool = False
    unusable: bool = False       # the model answered, but the critic's answer could not be used
    off_topic: bool = False
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Review:
    status: str                  # ok | off_topic | failed | unusable
    envelope: dict | None = None


def _score(lay: Layout, reqs: list, flags: dict, prd: str, opts: Options) -> tuple[dict, tuple]:
    own = questions_mod.open_questions(lay)
    inputs = rubric.Inputs(lay, tuple(reqs), flags, tuple(metric_lines(lay.body("metrics"))), own, word_count(prd),
                           opts.lenient)
    return rubric.evaluate(inputs), own


async def run(deps: TurnDeps, prd: str, opts: Options, policy: Sequence[str] = (), one_block: bool = False) -> Review:
    lines = number_lines(prd)
    lay, trace = layout(lines), Trace()
    headed = found_by_heading(lay)
    unheaded = one_block or headed < MIN_HEADED_SECTIONS
    missing = [k for k in REQUIRED if k not in lay.sections] if unheaded else []
    reqs = reqs_mod.split(lay.body("requirements"))
    try:
        values = await _crew(deps, lines, lay, reqs, prd, opts, missing, policy, one_block)
    except UnusableAnswer as e:  # a one-block PRD's locator, or an empty answer
        log.warning("review answer unusable: %s", e)
        if one_block:
            return Review("unusable")
        trace.unusable, values = True, None
    if values is None:
        if one_block:
            return Review("failed")
        trace.rubric_only, trace.locate_failed = True, bool(missing)
        return _finish(lay, reqs, {}, [], prd, opts, trace)
    if missing:
        lay = _apply_locate(lay, values.pop(0), trace)
        if one_block and trace.locate_failed:
            return Review("failed")
        reqs = reqs_mod.split(lay.body("requirements"))
    flags, asked = _apply_critic(values[0], values[1], reqs, trace)
    if trace.off_topic and headed <= OFF_TOPIC_MAX_HEADED:
        return Review("off_topic")
    trace.unclassified = max(0, len(reqs) - reqs_mod.MAX_CLASSIFIED) if not trace.rubric_only else 0
    return _finish(lay, reqs, flags, asked, prd, opts, trace)


async def _crew(deps: TurnDeps, lines: tuple[str, ...], lay: Layout, reqs: list, prd: str, opts: Options,
                missing: list[str], policy: Sequence[str], one_block: bool) -> list[Any] | None:
    """Each step's checked value ([locate,] critic, review; None = unusable even after the repair), or None when
    the model could not be reached."""
    if not deps.has_time():
        return None
    scored, _ = _score(lay, reqs, {}, prd, opts)
    gaps = json.dumps([g["issue"] for g in scored["gaps"][:MAX_GAPS_SHOWN]], ensure_ascii=False)
    numbered = numbered_block(lines)
    items = None if "requirements" in missing else "\n".join(f"{r.id}: {r.text}" for r in reqs[:reqs_mod.MAX_CLASSIFIED])
    steps = [prompts.critic_step(numbered, items, gaps, policy), prompts.review_step(numbered, gaps, policy)]
    checks = [Check(critique.critic, required=False), Check(_lead, required=False)]
    if missing:
        steps.insert(0, prompts.locate_step(numbered, [LABELS[k] for k in missing], policy))
        checks.insert(0, Check(lambda data: critique.located(data, missing), required=one_block))
    try:
        return await kickoff_checked(deps, steps, checks)
    except UnusableAnswer:
        raise
    except LlmError as e:
        log.warning("review crew failed: %s", e)
        return None


def _lead(data: Any) -> dict | None:
    """The review lead's lists; a declined review (`off_topic`) is a usable answer with nothing in it."""
    if isinstance(data, dict) and data.get("status") == critique.OFF_TOPIC:
        return {}
    return critique.review(data)


def _apply_locate(lay: Layout, ranges: Any, trace: Trace) -> Layout:
    if ranges == critique.OFF_TOPIC:
        trace.off_topic = True
        return lay
    if ranges is None:
        trace.locate_failed = True
        return lay
    trace.located = True
    return with_ranges(lay, ranges)


def _apply_critic(found: Any, lead: dict | None, reqs: list, trace: Trace) -> tuple[dict, list[dict]]:
    if found == critique.OFF_TOPIC:
        trace.off_topic = True
        return {}, []
    if found is None:
        trace.rubric_only = trace.unusable = True
        return {}, []
    lead = lead or None  # a declined review counts as no review
    trace.review_failed = lead is None
    return critique.apply_review(critique.flags_for(found["requirements"], reqs), found["questions"], lead)


def _finish(lay: Layout, reqs: list, flags: dict, asked: list[dict], prd: str, opts: Options, trace: Trace) -> Review:
    scored, own = _score(lay, reqs, flags, prd, opts)
    checked = questions_mod.check({"questions": asked}, lay.lines, own, scored["gaps"]) if asked else []
    return Review("ok", envelope_mod.build(scored, lay, reqs, own, checked, trace, opts))


async def classify_scope(deps: TurnDeps, text: str, policy: Sequence[str] = ()) -> str:
    """A short message with no PRD: off_topic, or needs_prd. A failed call counts as needs_prd (asking for
    the PRD never answers an off-topic question)."""
    def validate(data: Any) -> dict | None:
        ok = isinstance(data, dict) and data.get("status") in ("off_topic", "needs_prd")
        return data if ok else None
    try:
        (data,) = await kickoff_checked(deps, [prompts.scope_step(text, policy)], [Check(validate, required=False)])
    except LlmError as e:
        log.warning("scope check failed: %s", e)
        return "needs_prd"
    return data["status"] if data else "needs_prd"
