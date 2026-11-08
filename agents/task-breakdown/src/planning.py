"""The LLM-backed steps: a new plan (map when chunked, plan, one repair) and a revision (revise, one repair).
Everything after the model's JSON is code: normalise, list problems, reconcile ids, fix, finalize."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import checks
import mapping
import prompts
import revision
from finalize import Context, finalize
from inputs import BANDS, Brief, chunk, team_size
from llm import LlmError, TurnDeps, ask_json
from models import Plan

STATUSES = ("ok", "off_topic", "needs_info")
REVISION_BAND = (1, BANDS["fine"][1])
_CODE_CONSTRAINT = ("Team of ", "Deadline: ")


@dataclass(frozen=True)
class Outcome:
    status: str                 # ok | off_topic | needs_info | failed
    plan: Plan | None = None


def _valid(data: Any) -> dict | None:
    if not isinstance(data, dict) or data.get("status", "ok") not in STATUSES:
        return None
    status = data.get("status", "ok")
    if status == "ok" and not (isinstance(data.get("tasks"), list) and data["tasks"]):
        return None
    return {**data, "status": status}


async def _ask(deps: TurnDeps, messages: list[dict]) -> dict | None:
    """The call, and one retry from the repair budget when the answer is not valid JSON for the contract."""
    data = await ask_json(deps, messages, _valid)
    if data is None and deps.has_time() and deps.budget.take_repair():
        data = await ask_json(deps, messages, _valid)
    return data


async def _with_repair(deps: TurnDeps, draft: checks.Draft, band: tuple[int, int],
                       policy: Sequence[str]) -> checks.Draft:
    found = checks.problems(draft, band, policy)
    if not found or not deps.has_time() or not deps.budget.take_repair():
        return draft
    try:
        data = await ask_json(deps, prompts.repair_messages(draft.to_json(), found, policy), _valid)
    except LlmError:
        return draft
    if data is None or data["status"] != "ok":
        return draft
    repaired = checks.normalize(data)
    return repaired if repaired.tasks else draft


async def new_plan(deps: TurnDeps, brief: Brief, policy: Sequence[str], vague_used: bool = False) -> Outcome:
    chunks = chunk(brief.spec)
    notes: list[str] = []
    requirements: list[str] = []
    spec = brief.spec
    if brief.spec_cut:
        notes.append(f"Your message was {brief.spec_cut:,} characters over the 100,000 I read; the rest wasn't used.")
    if len(chunks) > 1:
        requirements, answered = await mapping.requirements(deps, chunks)
        spec = ""
        notes.append(f"Planned from the whole spec ({len(chunks)} parts)." if answered == len(chunks) else
                     f"Planned from {answered} of {len(chunks)} parts of the spec; the others could not be read.")
    band = BANDS[brief.granularity]
    messages = prompts.plan_messages(brief.goal, spec, requirements, [], band, policy, may_ask=not vague_used)
    try:
        data = await _ask(deps, messages)
    except LlmError:
        return Outcome("failed")
    if data is None:
        return Outcome("failed")
    if data["status"] != "ok":
        return Outcome(data["status"])
    draft = await _with_repair(deps, checks.normalize(data), band, policy)
    ctx = Context(goal=brief.goal, source=brief.source, band=band, policy=tuple(policy), deadline=brief.deadline,
                  team_size=brief.team_size, chunks=len(chunks) if len(chunks) > 1 else 0, vague_used=vague_used,
                  notes=tuple(notes))
    return Outcome("ok", finalize(draft, ctx).model_copy(update={"granularity": brief.granularity}))


def plan_for_model(plan: Plan) -> dict:
    return {"goal": plan.goal, "phases": [{"id": p.number, "name": p.name} for p in plan.phases],
            "tasks": [t.model_dump(mode="json", exclude={"status"}) for t in plan.tasks],
            "constraints": list(plan.constraints),
            "risks": [{"task": r.task, "text": r.text} for r in plan.risks],
            "assumptions": [{"text": a.text, "based_on": a.based_on} for a in plan.assumptions]}


async def revise(deps: TurnDeps, plan: Plan, instruction: str, policy: Sequence[str]) -> Outcome:
    messages = prompts.revise_messages(plan_for_model(plan), instruction, REVISION_BAND, policy)
    try:
        data = await _ask(deps, messages)
    except LlmError:
        return Outcome("failed")
    if data is None:
        return Outcome("failed")
    if data["status"] != "ok":
        return Outcome(data["status"])
    draft, _, first_notes = revision.reconcile(plan, checks.normalize(data), instruction)
    draft = await _with_repair(deps, draft, REVISION_BAND, policy)
    draft, next_id, notes = revision.reconcile(plan, draft, instruction)
    notes = tuple(dict.fromkeys((*first_notes, *notes)))
    ctx = Context(goal=plan.goal, source=f"{plan.source}\n{instruction}", band=REVISION_BAND, policy=tuple(policy),
                  renumber=False, next_id=next_id,
                  known_constraints=tuple(c for c in plan.constraints if not c.startswith(_CODE_CONSTRAINT)),
                  deadline=plan.deadline, team_size=team_size(instruction) or plan.team_size, rate=plan.rate,
                  chunks=plan.chunks, vague_used=plan.vague_used, notes=notes)
    return Outcome("ok", finalize(draft, ctx).model_copy(update={"granularity": plan.granularity}))
