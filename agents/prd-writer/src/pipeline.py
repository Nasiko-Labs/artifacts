"""One model-calling turn: a new PRD (extract -> [ask who] -> compose -> check -> [repair] -> render), a
revision of the stored one, or the scope check of a short message. Returns the state update.

Call budgets: single brief 4 (extract, compose, + 1 repair, + 1 JSON repair), chunked brief 5, revision 2.
On the turn deadline the extracted facts are rendered as a "what I could extract" list."""

from __future__ import annotations

import logging
from collections.abc import Sequence

import envelope
import facts as facts_step
import pauses
import prompts
import render
import revise as revise_step
import smalltalk
from compose import Composed, Grounds, compose
from inputs import Incoming, parse_instruction, word_count
from llm import DeadlineError, LlmError, TurnDeps, complete_json
from prd import Fact, Prd, facts_from_state, facts_to_state

log = logging.getLogger("prd-writer.pipeline")

SINGLE_BUDGET, CHUNKED_BUDGET, REVISION_BUDGET = 4, 5, 2


def corpus(brief: str, instruction: str, answers: Sequence[str] = ()) -> str:
    return "\n\n".join(p for p in (brief, instruction, *answers) if p)


def title_for(instruction: str, facts: Sequence[Fact]) -> str:
    named = parse_instruction(instruction).title
    if named:
        return named
    problem = next((f for f in facts if f.kind == "problem"), None)
    return " ".join(problem.text.split()[:6]).rstrip(".,;:") if problem else "Untitled"


def _limit(deps: TurnDeps, calls: int) -> None:
    deps.budget.limit = min(deps.budget.limit, deps.budget.used + calls)


async def scope_check(deps: TurnDeps, incoming: Incoming, pending: pauses.Pending) -> dict:
    """A short message with no PRD cue: decline it, or ask for a fuller brief."""
    try:
        raw = await complete_json(deps, prompts.scope_messages(incoming.text, incoming.policy),
                                  prompts.SCOPE_CONTRACT, _scope_valid)
    except LlmError:
        return {"reply": prompts.MODEL_FAILED}
    if raw["status"] == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    return {"pause": pending.to_metadata()}


def _scope_valid(data: object) -> dict | None:
    return data if isinstance(data, dict) and data.get("status") in ("prd", "off_topic") else None


async def new_prd(deps: TurnDeps, incoming: Incoming, brief: str, instruction: str) -> dict:
    chunked = len(facts_step.chunks(brief)) > 1
    _limit(deps, CHUNKED_BUDGET if chunked else SINGLE_BUDGET)
    try:
        extracted = await facts_step.extract(deps, brief, instruction, incoming.policy)
    except DeadlineError:
        return {"reply": prompts.DEADLINE_NOTE}
    except LlmError:
        return {"reply": prompts.MODEL_FAILED}
    if extracted.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    if not extracted.facts:
        return {"reply": prompts.NO_FACTS}
    notes = _coverage(extracted, brief)
    audience = parse_instruction(instruction).audience
    if not facts_step.has_kind(extracted.facts, "user") and not audience:
        pending = pauses.Pending(pauses.WHO, brief=brief, instruction=instruction,
                                 facts=facts_to_state(extracted.facts), wants_json=incoming.wants_json)
        return {"pause": pending.to_metadata()}
    return await compose_prd(deps, incoming, brief, instruction, extracted.facts, (), notes)


async def answer_who(deps: TurnDeps, incoming: Incoming, pending: pauses.Pending, audience: str) -> dict:
    _limit(deps, SINGLE_BUDGET - 1)
    known = facts_step.answer_fact(audience, facts_from_state(pending.facts))
    return await compose_prd(deps, incoming, pending.brief, pending.instruction, known, (audience,), ())


async def compose_prd(deps: TurnDeps, incoming: Incoming, brief: str, instruction: str, known: tuple[Fact, ...],
                      answers: Sequence[str], notes: Sequence[str]) -> dict:
    audience = parse_instruction(instruction).audience or (answers[0] if answers else "")
    grounds = Grounds(known, corpus(brief, instruction, answers), audience, incoming.policy)
    try:
        result = await compose(deps, grounds, instruction, title_for(instruction, known))
    except LlmError as e:
        log.info("compose did not finish: %s", e)
        return {"reply": render.render_facts(known, prompts.DEADLINE_NOTE if isinstance(e, DeadlineError)
                                             else prompts.MODEL_FAILED)}
    if result.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    stored = {"brief": brief, "instruction": instruction, "answers": list(answers),
              "facts": facts_to_state(known), "notes": list(notes)}
    return {**stored, **_finished(result, stored, incoming.wants_json, parse_instruction(instruction).cap, notes)}


async def revise_prd(deps: TurnDeps, incoming: Incoming, state: dict, request: str) -> dict:
    _limit(deps, REVISION_BUDGET)
    known = facts_from_state(state.get("facts"))
    answers = tuple(state.get("answers") or ())
    instruction = state.get("instruction") or ""
    audience = parse_instruction(instruction).audience or (answers[0] if answers else "")
    grounds = Grounds(known, corpus(state.get("brief") or "", instruction, answers), audience, incoming.policy)
    try:
        result, restored = await revise_step.revise(deps, Prd.from_dict(state.get("prd")), grounds, request)
    except LlmError:
        return {"reply": prompts.MODEL_FAILED}
    if result.status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY}
    notes = ["I changed only the section you named and kept the others as they were."] if restored else []
    return _finished(result, state, incoming.wants_json, state.get("cap") or parse_instruction(instruction).cap,
                     notes)


def _finished(result: Composed, stored: dict, wants_json: bool, cap: int, notes: Sequence[str]) -> dict:
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
