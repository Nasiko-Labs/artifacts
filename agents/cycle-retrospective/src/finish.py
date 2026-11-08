"""The finish step: spill reasons (reasons.py), then everything computed in code (metrics, discussion
prompts, confidence) and rendered (markdown + envelope); with a channel named, the post card."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import render
from deps import TurnDeps
from metrics import split
from prompts_retro import discussion_prompts
from reasons import reasons_step
from records import Cycle, Evidence, Issue, Team, from_state
from retro import Quality, Retro, confidence, needs_human
from steps_followup import offer_post


async def finish_step(deps: TurnDeps, state: Mapping[str, Any]) -> dict:
    cycle, team = from_state(Cycle, state["cycle"]), from_state(Team, state["team"])
    parts = split(from_state(Issue, state.get("issues") or []), cycle, deps.now, deps.config.planned_grace_hours)
    found = await reasons_step(deps, parts.spilled, from_state(Evidence, state.get("evidence") or []))
    spills, reason_notes = found.spills, found.notes
    notes = render.notes_for(parts.metrics, deps.config.planned_grace_hours,
                             [*(state.get("notes") or []), *reason_notes])
    q = dict(state.get("quality") or {})
    quality = Quality(tracker_read=True, chat_searched=bool(q.get("chat_searched", True)),
                      gaps=bool(q.get("gaps")), capped=bool(q.get("capped")), extracted=int(q.get("extracted") or 0) + found.labelled,
                      dropped=int(q.get("dropped") or 0) + found.dropped, salvaged=bool(q.get("salvaged")) or bool(reason_notes),
                      created_fallback=parts.metrics.created_fallback)
    level = confidence(quality)
    retro = Retro(team=team, cycle=cycle, metrics=parts.metrics, shipped=parts.completed, added=parts.added,
                  spills=spills, prompts=discussion_prompts(parts.metrics, spills), notes=notes, confidence=level,
                  needs_human=needs_human(level, spills))
    markdown = render.retro_markdown(retro)
    update = {"retro": retro.to_state(), "markdown": markdown, "reply": markdown,
              "envelope": render.envelope(retro, markdown), "route": ""}
    channel = state.get("channel") or ""
    if not channel or not (state.get("req") or {}).get("channel"):
        return update
    return {**update, **await offer_post(deps, retro, channel, markdown)}
