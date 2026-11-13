"""One model-calling turn, as ONE crew kickoff: a new PRD (fact extractor -> PRD author -> PRD editor, 3 calls),
a revision (`revision.py`) or the scope check of a short message (1 call). Code does the rest: verify the facts,
pick the editor's PRD (else the author's), check it, add gap questions, render.

An answer that is not JSON in its step's shape gets the turn's one repair call (`repair.read`); still unusable
-> MODEL_UNUSABLE (with the facts list when the facts were read). Stateless: nothing is stored, so a follow-up
command rebuilds the PRD from the brief (or, when that was trimmed, our last draft) recalled from the history.
When the crew fails after the extractor finished, the extracted facts are rendered as a "what I could extract"
list."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import envelope
import facts as facts_step
import parse
import prompts
import render
import repair
import smalltalk
from compose import Composed, Grounds, compose
from inputs import Incoming, parse_instruction, word_count
from llm import DeadlineError, LlmError, Step, TurnDeps, UnusableAnswer, kickoff, run_crew
from prd import facts_from_state, facts_to_state

log = logging.getLogger("prd-writer-stateless.pipeline")

PRD_CHECK = repair.Check(parse.validate)


@dataclass(frozen=True)
class Built:
    """The crew's answers read back: the checked facts, the steps and every step's raw output (repaired ones
    included), so a later step can still be repaired."""

    extracted: facts_step.Extracted
    steps: tuple[Step, ...]
    raws: tuple[str, ...]


def corpus(brief: str, instruction: str, answers: Sequence[str] = ()) -> str:
    return "\n\n".join(p for p in (brief, instruction, *answers) if p)


def title_for(instruction: str) -> str:
    """The title the user named ("PRD for audit export"), or "" to let `title.feature_title` name the feature."""
    return parse_instruction(instruction).title


async def scope_check(deps: TurnDeps, incoming: Incoming) -> dict:
    """A short message with no PRD cue: decline it, or ask for a fuller brief (no pause: a plain reply)."""
    status = await scope_status(deps, incoming)
    if status == "failed":
        return {"reply": prompts.MODEL_FAILED}
    return {"reply": smalltalk.OFF_TOPIC_REPLY if status == "off_topic" else prompts.NEED_MORE}


async def scope_status(deps: TurnDeps, incoming: Incoming) -> str:
    """"off_topic", "prd", or "failed" (unreachable). An answer still unusable after the repair counts as "prd",
    so a real request is never declined by a garbled answer."""
    step = prompts.scope_step(incoming.text, incoming.policy)
    try:
        raws = await kickoff(deps, [step])
        data, _ = await repair.read(deps, [step], raws, 0, repair.Check(_scope_status))
    except LlmError:
        return "failed"
    return "off_topic" if data == "off_topic" else "prd"


async def scope_gate(deps: TurnDeps, incoming: Incoming) -> str:
    """The scope check on the turn's spare (repair) call, for a revision that needs all three regular calls.
    No repair of its own: an unusable answer, or no spare call left, counts as "prd"."""
    if not deps.budget.take_repair():
        return "prd"
    try:
        raws = await run_crew(deps, [prompts.scope_step(incoming.text, incoming.policy)])
    except LlmError:
        return "failed"
    return "off_topic" if repair.usable(repair.Check(_scope_status), raws[0]) == "off_topic" else "prd"


async def new_prd(deps: TurnDeps, incoming: Incoming, brief: str, instruction: str) -> dict:
    """A new PRD, or the PRD rebuilt from recalled material for a follow-up command."""
    built = await crew(deps, incoming, brief, instruction, prompts.compose_step(instruction, incoming.policy),
                       prompts.edit_step(incoming.policy))
    if isinstance(built, dict):
        return built
    try:
        author, editor = await _author_and_editor(deps, built)
    except LlmError as e:  # the model could not be reached for the repair
        log.info("repair did not finish: %s", e)
        return {"reply": render.render_facts(built.extracted.facts, prompts.MODEL_FAILED)}
    if author is None and editor is None:
        return {"reply": render.render_facts(built.extracted.facts, prompts.MODEL_UNUSABLE)}
    return composed(incoming, brief, instruction, built.extracted,
                    lambda g, t: (compose(author, editor, g, t), ()))


async def _author_and_editor(deps: TurnDeps, built: Built) -> tuple[dict | None, dict | None]:
    """The author's and editor's validated PRDs. An editor that ran gets the repair when its answer is unusable
    (the author's draft is the fallback); an unusable author (the editor then skipped) gets it otherwise."""
    steps, raws = built.steps, built.raws
    author, editor = repair.usable(PRD_CHECK, raws[1]), repair.usable(PRD_CHECK, raws[2])
    if editor is None and raws[2]:
        editor, _ = await repair.read(deps, steps, raws, 2, PRD_CHECK)
    elif author is None and editor is None:
        author, raws = await repair.read(deps, steps, raws, 1, PRD_CHECK)
        editor = repair.usable(PRD_CHECK, raws[2]) if author is not None else None
    return author, editor


async def crew(deps: TurnDeps, incoming: Incoming, brief: str, instruction: str, *later: Step) -> Built | dict:
    """extractor -> `later`: the facts read back (repaired once if unusable), or a reply to send instead."""
    text, parts, total = facts_step.brief_parts(brief)
    steps = (prompts.extract_step(text, instruction, incoming.policy, parts), *later)
    try:
        outputs = await kickoff(deps, steps)
        check = repair.Check(lambda data: facts_step.read(data, brief, parts, total))
        extracted, outputs = await repair.read(deps, steps, outputs, 0, check)
    except LlmError as e:
        log.info("crew did not finish: %s", e)
        return _partial(e, brief, parts, total)
    if extracted is None:
        return {"reply": prompts.MODEL_UNUSABLE}
    if extracted.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if not extracted.facts:
        return {"reply": prompts.NO_FACTS}
    return Built(extracted, steps, tuple(outputs))


def _scope_status(data: object) -> str | None:
    status = data.get("status") if isinstance(data, dict) else None
    return status if status in ("prd", "off_topic") else None


def _partial(error: LlmError, brief: str, parts: int, total: int) -> dict:
    if isinstance(error, DeadlineError):
        note = prompts.DEADLINE_NOTE
    else:
        note = prompts.MODEL_UNUSABLE if isinstance(error, UnusableAnswer) else prompts.MODEL_FAILED
    raw = error.partial[0] if error.partial else ""
    extracted = repair.usable(repair.Check(lambda data: facts_step.read(data, brief, parts, total)), raw)
    if extracted is None or not extracted.facts:
        return {"reply": note}
    return {"reply": render.render_facts(extracted.facts, note)}


def composed(incoming: Incoming, brief: str, instruction: str, extracted: facts_step.Extracted,
              make: Callable[[Grounds, str], tuple[Composed, tuple[str, ...]]], revision: bool = False) -> dict:
    """`make` builds the checked PRD and any notes of its own; a revision shows only those notes."""
    audience = parse_instruction(instruction).audience
    notes = () if revision else _coverage(extracted, brief)
    grounds = Grounds(extracted.facts, corpus(brief, instruction), audience, incoming.policy)
    try:
        result, own = make(grounds, title_for(instruction))
        notes = (*notes, *own)
    except ValueError as e:
        log.info("no usable PRD: %s", e)
        return {"reply": render.render_facts(extracted.facts, prompts.MODEL_UNUSABLE)}
    if result.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    named = facts_step.has_kind(extracted.facts, "user") or any(not u.proposed for u in result.prd.users)
    if not revision and not named and not audience:
        notes = (*notes, prompts.NO_AUDIENCE_NOTE)  # was a pause: say so, don't guess
    stored = {"brief": brief, "instruction": instruction, "answers": [],
              "facts": facts_to_state(extracted.facts), "notes": list(notes)}
    return {**stored, **finished(result, stored, incoming.wants_json, parse_instruction(instruction).cap, notes)}


def finished(result: Composed, stored: dict, wants_json: bool, cap: int, notes: Sequence[str]) -> dict:
    view = render.View("full", cap)
    known = facts_from_state(stored.get("facts"))
    markdown = render.render(result.prd, known, view, result.dropped, notes)
    env = envelope.build(result.prd, known, word_count(stored.get("brief") or ""), markdown, result.dropped)
    reply = envelope.as_json_text(env) if wants_json else markdown
    return {"prd": result.prd.to_dict(), "dropped": result.dropped, "cap": cap, "envelope": env, "reply": reply,
            "data": env}


def _coverage(extracted: facts_step.Extracted, brief: str) -> tuple[str, ...]:
    if extracted.parts >= extracted.total:
        return ()
    note = (f"Your brief is about {len(brief):,} characters; I read the first {extracted.covered_chars:,} "
            f"({extracted.parts} parts). Paste the rest in a new message to cover it.")
    return (note,)

