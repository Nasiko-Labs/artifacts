"""A revision ("make R2 testable", "shorter problem"), as ONE crew kickoff.

It starts from the PRD the user last saw, read back from the history (`recall.Prior.shown`): extractor ->
reviser (2 calls) with that PRD in the reviser's CURRENT_PRD block; code keeps every entry the user already saw
as shown and checks only what the reviser added or changed (`revise.revise_shown`), so edits the editor made
earlier never come back. Only when that PRD is no longer in the history is the PRD rebuilt from the brief first
(extractor -> author -> reviser, 3 calls), and the reply says so in one sentence. An unusable answer gets the
turn's one repair call (`repair.read`)."""

from __future__ import annotations

import logging

import pipeline
import prompts
import render
import repair
import revise
import shown as shown_mod
from compose import Composed, Grounds, compose
from inputs import Incoming
from llm import LlmError, TurnDeps
from prd import Prd

log = logging.getLogger("prd-writer-stateless.revision")

RESTORED_NOTE = "I changed only the section you named and kept the others as they were."


async def revise_shown_prd(deps: TurnDeps, incoming: Incoming, brief: str, instruction: str, request: str,
                           shown: Prd) -> dict:
    """The PRD the user last saw, revised as asked."""
    step = prompts.revise_shown_step(request, revise.named_sections(request), shown_mod.as_json(shown),
                                     incoming.policy)
    built = await pipeline.crew(deps, incoming, brief, instruction, step)
    if isinstance(built, dict):
        return built
    try:
        reviser, _ = await repair.read(deps, built.steps, built.raws, 1, pipeline.PRD_CHECK)
    except LlmError as e:
        log.info("repair did not finish: %s", e)
        return {"reply": render.render_facts(built.extracted.facts, prompts.MODEL_FAILED)}
    if reviser is None:
        return {"reply": render.render_facts(built.extracted.facts, prompts.MODEL_UNUSABLE)}

    def run(grounds: Grounds, title: str) -> tuple[Composed, tuple[str, ...]]:
        result, restored = revise.revise_shown(shown, reviser, grounds, request)
        return result, (RESTORED_NOTE,) if restored else ()

    return pipeline.composed(incoming, brief, instruction, built.extracted, run, revision=True)


async def rebuild_and_revise(deps: TurnDeps, incoming: Incoming, brief: str, instruction: str, request: str) -> dict:
    """The fallback: the PRD rebuilt from recalled material and revised as asked, in one kickoff."""
    step = prompts.revise_step(request, revise.named_sections(request), incoming.policy)
    built = await pipeline.crew(deps, incoming, brief, instruction,
                                prompts.compose_step(instruction, incoming.policy), step)
    if isinstance(built, dict):
        return built
    try:
        author, reviser = await _author_and_reviser(deps, built)
    except LlmError as e:
        log.info("repair did not finish: %s", e)
        return {"reply": render.render_facts(built.extracted.facts, prompts.MODEL_FAILED)}
    if author is None or reviser is None:
        return {"reply": render.render_facts(built.extracted.facts, prompts.MODEL_UNUSABLE)}

    def run(grounds: Grounds, title: str) -> tuple[Composed, tuple[str, ...]]:
        stored = compose(author, None, grounds, title)
        if stored.status != "ok":
            return stored, ()
        result, restored = revise.revise(stored.prd, reviser, grounds, request)
        return result, (prompts.REBUILT_NOTE, *((RESTORED_NOTE,) if restored else ()))

    return pipeline.composed(incoming, brief, instruction, built.extracted, run, revision=True)


async def _author_and_reviser(deps: TurnDeps, built: pipeline.Built) -> tuple[dict | None, dict | None]:
    """An unusable author answer (the reviser then skipped) is repaired with the reviser run behind it; else an
    unusable reviser answer is repaired on its own."""
    steps, raws = built.steps, built.raws
    author = repair.usable(pipeline.PRD_CHECK, raws[1])
    if author is None:
        author, raws = await repair.read(deps, steps, raws, 1, pipeline.PRD_CHECK)
        return author, repair.usable(pipeline.PRD_CHECK, raws[2]) if author is not None else None
    if author.get("status") == "off_topic":
        return author, {"status": "off_topic"}
    reviser, _ = await repair.read(deps, steps, raws, 2, pipeline.PRD_CHECK)
    return author, reviser
