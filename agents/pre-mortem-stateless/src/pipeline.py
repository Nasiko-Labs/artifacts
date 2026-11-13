"""One pre-mortem run: one crew kickoff (failure-mode finder -> skeptical reviewer, 2 model calls), then code:
apply the reviewer's ratings and fixes, check every story (`specific.assess`), dedup, merge, rank. An unusable
finder or reviewer answer gets the turn's one repair call (`llm.kickoff_checked`); a finder answer still unusable
after it ends the run as `unusable`, a reviewer answer still unusable leaves the stories unmerged."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

import prompts
from consolidate import apply, unmerged
from findings import OFF_TOPIC, apply_review, read_find, read_review
from grounding import normalise
from llm import LlmError, TurnDeps, UnusableAnswer
from plan import numbered_block
from rank import Story, rank
from repair import Check, kickoff_checked
from specific import OK, Candidate, assess, dedup

log = logging.getLogger("pre-mortem.pipeline")


@dataclass(frozen=True)
class Outcome:
    status: str                              # ok | off_topic | failed | unusable
    stories: tuple[Story, ...] = ()          # ranked, top N
    assumptions: tuple[dict, ...] = ()
    questions: tuple[str, ...] = ()
    survivors: tuple[Candidate, ...] = ()    # every checked candidate
    left_out: int = 0
    voice_failures: int = 0                  # kept for the envelope: the finder covers all lenses in one call
    consolidate_ok: bool = True              # False when the reviewer's answer was unusable


def ground_of(lines: Sequence[str], context: Sequence[str]) -> str:
    return normalise("\n".join([*lines, *context]))


async def run(deps: TurnDeps, lines: tuple[str, ...], context: tuple[str, ...], policy: Sequence[str] = (),
              count: int = 7, focus: str = "") -> Outcome:
    numbered = numbered_block(lines)
    steps = [prompts.find_step(numbered, context, policy), prompts.review_step(numbered, context, policy, focus)]
    try:
        found, review = await kickoff_checked(deps, steps, [Check(read_find), Check(_read_review, required=False)])
    except UnusableAnswer as e:
        log.warning("pre-mortem answer unusable: %s", e)
        return Outcome("unusable")
    except LlmError as e:
        log.warning("pre-mortem crew failed: %s", e)
        return Outcome("failed")
    status, candidates = found
    if status == OFF_TOPIC:
        return Outcome("off_topic")
    review = review or None  # a declined review counts as no review
    ground = ground_of(lines, context)
    survivors = dedup(_screen(apply_review(candidates, review), lines, ground))
    merged = apply(review, survivors, lines, ground) if review else unmerged(survivors)
    ranked = rank(merged.stories, count, focus)
    return Outcome("ok", tuple(ranked), merged.assumptions, merged.questions, tuple(survivors),
                   left_out=max(0, len(candidates) - len(survivors)), consolidate_ok=review is not None)


def _read_review(data) -> dict | None:
    """The reviewer's lists; a declined review (`off_topic`) is a usable answer with nothing in it."""
    if isinstance(data, dict) and data.get("status") == OFF_TOPIC:
        return {}
    return read_review(data)


def _screen(candidates: Sequence[Candidate], lines: tuple[str, ...], ground: str) -> list[Candidate]:
    """Only stories that pass every check; filler fields the reviewer did not fix are dropped."""
    kept: list[Candidate] = []
    for candidate in candidates:
        verdict, located = assess(candidate, lines, ground)
        if verdict == OK:
            kept.append(located)
        else:
            log.info("dropped %s: %s", candidate.id, verdict)
    return kept

