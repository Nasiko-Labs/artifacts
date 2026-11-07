"""The one consolidate call and the pure code that applies its answer.

The model may only (a) group ids that describe the same failure and (b) write unstated assumptions and
questions. A group keeps its highest-scoring member and records every lens that raised it; an id the model
made up is ignored; a story the model writes is never read. An assumption must quote the plan line it rests on
(`related_ref`); a question must trace to a story id or an assumption id."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import prompts
from grounding import ungrounded
from llm import LlmError, TurnDeps, complete_json
from rank import Story, lenses_in_order, score
from specific import Candidate, locate_line

log = logging.getLogger("pre-mortem.consolidate")

MAX_ASSUMPTIONS = 6
MAX_QUESTIONS = 8
MAX_TEXT_WORDS = 45


@dataclass(frozen=True)
class Consolidated:
    stories: tuple[Story, ...]
    assumptions: tuple[dict, ...] = ()
    questions: tuple[str, ...] = ()
    ok: bool = True


def unmerged(survivors: Sequence[Candidate]) -> Consolidated:
    """What renders when the consolidate call failed: every survivor on its own, no lists."""
    return Consolidated(tuple(Story(c, (c.lens,)) for c in survivors), ok=False)


def validate(data: Any) -> dict | None:
    if not isinstance(data, dict):
        return None
    lists = {k: data.get(k) or [] for k in ("merge_groups", "unstated_assumptions", "questions_for_the_team")}
    return lists if all(isinstance(v, list) for v in lists.values()) else None


async def consolidate(deps: TurnDeps, survivors: Sequence[Candidate], lines: tuple[str, ...], numbered: str,
                      context: Sequence[str], norm_ground: str, policy: Sequence[str] = (),
                      focus: str = "") -> Consolidated:
    if not survivors or not deps.has_time():
        return unmerged(survivors)
    block = json.dumps([{"id": c.id, "title": c.title, "story": c.story, "plan_line": f"L{c.line}",
                         "likelihood": c.likelihood, "impact": c.impact} for c in survivors], ensure_ascii=False)
    messages = prompts.consolidate_messages(numbered, context, block, policy, focus)
    try:
        data = await complete_json(deps, messages, prompts.CONSOLIDATE_CONTRACT, validate)
    except LlmError as e:
        log.warning("consolidate failed: %s", e)
        return unmerged(survivors)
    return apply(data, survivors, lines, norm_ground)


def apply(data: dict, survivors: Sequence[Candidate], lines: tuple[str, ...], norm_ground: str) -> Consolidated:
    stories = _merge(data.get("merge_groups") or [], survivors)
    assumptions, kept_ids = _assumptions(data.get("unstated_assumptions") or [], lines, norm_ground)
    traceable = {c.id for c in survivors} | kept_ids
    questions = _questions(data.get("questions_for_the_team") or [], traceable, norm_ground)
    return Consolidated(tuple(stories), tuple(assumptions), tuple(questions))


def _merge(groups: list, survivors: Sequence[Candidate]) -> list[Story]:
    by_id = {c.id: c for c in survivors}
    order = {c.id: i for i, c in enumerate(survivors)}
    used: set[str] = set()
    merged: dict[str, Story] = {}
    for group in groups:
        ids = [i for i in group if isinstance(i, str) and i in by_id and i not in used] if isinstance(group, list) else []
        ids = list(dict.fromkeys(ids))
        if len(ids) < 2:
            continue
        members = [by_id[i] for i in ids]
        best = min(members, key=lambda c: (-score(c), order[c.id]))
        merged[best.id] = Story(best, lenses_in_order(c.lens for c in members))
        used.update(ids)
    return [merged.get(c.id) or Story(c, (c.lens,)) for c in survivors if c.id not in used or c.id in merged]


def _assumptions(raw: list, lines: tuple[str, ...], norm_ground: str) -> tuple[list[dict], set[str]]:
    kept: list[dict] = []
    ids: set[str] = set()
    seen: set[str] = set()
    for n, item in enumerate(raw, start=1):
        if not isinstance(item, dict) or len(kept) >= MAX_ASSUMPTIONS:
            continue
        text = " ".join(str(item.get("text") or "").split())
        line = locate_line(str(item.get("related_ref") or ""), lines)
        if not text or line is None or len(text.split()) > MAX_TEXT_WORDS or ungrounded(text, norm_ground):
            continue
        if text.casefold() in seen:
            continue
        seen.add(text.casefold())
        kept.append({"text": text, "plan_line": line})
        ids.add(f"a{n}")
    return kept, ids


def _questions(raw: list, traceable: set[str], norm_ground: str) -> list[str]:
    kept: list[str] = []
    for item in raw:
        if not isinstance(item, dict) or len(kept) >= MAX_QUESTIONS:
            continue
        text = " ".join(str(item.get("text") or "").split())
        about = str(item.get("about") or "").strip()
        if not text or about not in traceable or len(text.split()) > MAX_TEXT_WORDS:
            continue
        if ungrounded(text, norm_ground) or text in kept:
            continue
        kept.append(text)
    return kept
