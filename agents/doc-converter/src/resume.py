"""Pure-ish: turn an answer to a paused question into the next step.

Q1: a place label continues with that place; "I'll paste it" asks for the paste; other text is a file
name or link (its place read from the words or the link, else both places are searched). Q2: the exact
option label picks that file; other text is a more exact name. Empty: nothing converted."""

from __future__ import annotations

from dataclasses import replace

import parse
import prompts
import sources
from deps import TurnDeps
from flow import Scope
from hitl import Pause, chosen
from inputs import Incoming


def next_step(pause: Pause, answer: str, deps: TurnDeps) -> tuple[Scope | None, str]:
    """(scope, entry route) to run, or (None, fixed reply)."""
    text = answer.strip()
    job = pause.job
    if not text:
        if pause.kind == "match" and job.resolved:
            return Scope(deps=deps, incoming=Incoming(""), job=job), "locate"
        return None, prompts.NOTHING_CONVERTED
    if pause.kind == "source":
        if text.casefold() == sources.PASTE_OPTION.casefold():
            return None, prompts.PASTE_NEXT
        place = sources.place_from_answer(text)
        if text.casefold() not in (sources.LABEL[p].casefold() for p in sources.PLACES):
            typed = parse.literal(text).files or ({"query": text, "link": None},)
            job = replace(job, requests=typed)
        job = replace(job, place=place)
    else:
        picked = chosen(pause, text)
        if picked is not None:
            job = replace(job, resolved=(*job.resolved, picked.to_dict()))
        else:
            typed = parse.literal(text).files or ({"query": text, "link": None},)
            job = replace(job, requests=(*typed, *job.requests))
    return Scope(deps=deps, incoming=Incoming(""), job=job), "locate"
