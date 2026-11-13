"""One draft = one crew kickoff, 3 model calls: requirements analyst -> software architect -> technical writer
(the writer does the ADR in ADR mode). Then code: verify and number the requirements, map the analyst's ids
onto them, clean the design and each section group. A follow-up runs the same crew on the recalled
requirements with the user's request (and answers) in a REQUEST block, so the draft is rebuilt with it.
An answer that is not JSON in its step's shape gets the turn's one repair call (`repair.repair`); still
unusable -> `unusable` (the model answered, but its answer could not be used)."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import design as design_mod
import extract as extract_mod
import prompts
import sections as sections_mod
from inputs import Request, is_agent_system
from llm import LlmError, TurnDeps, UnusableAnswer, kickoff, load
from repair import Check, repair

log = logging.getLogger("design-doc-drafter.pipeline")


@dataclass(frozen=True)
class Drafted:
    status: str                  # ok | off_topic | empty | failed | unusable
    doc: dict | None = None


def ids_of(doc: dict) -> list[str]:
    return [r["id"] for r in doc["reqs"]]


class _Crew:
    """One kickoff's steps and raw outputs, read in order; the first unusable answer gets the turn's one repair
    (`repair.repair`), later ones only if it is still unspent (it never is twice)."""

    def __init__(self, deps: TurnDeps, steps: Sequence, raws: Sequence[str]):
        self.deps, self.steps, self.raws = deps, steps, raws

    async def read(self, index: int, check: Check) -> Any:
        value = check.validate(load(self.raws[index]))
        if value is not None:
            return value
        log.warning("step %d answer unusable (%d chars); repairing once", index + 1, len(self.raws[index]))
        return await repair(self.deps, self.steps, self.raws, index, check)


async def draft(deps: TurnDeps, request: Request, policy: Sequence[str], instruction: str = "") -> Drafted:
    text, skipped = extract_mod.readable(request.requirements)
    blocks = {"CONTEXT": "\n".join(request.context), "GUIDANCE": request.guidance or "", "REQUEST": instruction}
    agent = is_agent_system(request.requirements)
    steps = [prompts.extract_step(text, policy, instruction), prompts.design_step(blocks, policy, request.short),
             prompts.sections_step(blocks, policy, agent, request.short, request.adr)]
    try:
        crew = _Crew(deps, steps, await kickoff(deps, steps))
        return await _read(crew, request, skipped, agent)
    except UnusableAnswer as e:
        log.warning("draft answer unusable: %s", e)
        return Drafted("unusable")
    except LlmError as e:
        log.warning("draft crew failed: %s", e)
        return Drafted("failed")


async def _read(crew: _Crew, request: Request, skipped: int, agent: bool) -> Drafted:
    ex = await crew.read(0, Check(lambda data: extract_mod.read(data, request.requirements, skipped)))
    if ex is None:
        return Drafted("unusable")
    if ex.status == "off_topic":
        return Drafted("off_topic")
    if not ex.requirements:
        return Drafted("empty")
    doc = {"title": ex.title, "source": request.requirements, "context": list(request.context),
           "guidance": request.guidance, "answers": [], "reqs": list(ex.requirements), "agent": agent,
           "adr": request.adr, "short": request.short, "qnums": {}, "answered": [], "repaired": False,
           "dropped_requirements": ex.dropped, "skipped_chars": ex.skipped_chars}
    return await _design_and_sections(crew, doc, ex.id_map or {})


async def _design_and_sections(crew: _Crew, doc: dict, id_map: dict) -> Drafted:
    ids = ids_of(doc)
    proposal = await crew.read(1, Check(lambda data: design_mod.validator(ids)(extract_mod.remap(data, id_map))))
    if proposal is None:
        log.warning("architect answer did not match the contract")
        return Drafted("unusable")
    if proposal["status"] == "off_topic":
        return Drafted("off_topic")
    keys = ["ADR"] if doc["adr"] else ["A", "B", "C"]

    def sections(data: Any) -> tuple | None:
        read = sections_mod.read(extract_mod.remap(data, id_map), keys, ids)
        return None if read[2] == len(keys) else read  # usable when at least one group is valid

    read = await crew.read(2, Check(sections))
    if read is None:
        return Drafted("unusable")
    status, secs, _ = read
    if status == "off_topic":
        return Drafted("off_topic")
    design = {k: v for k, v in proposal.items() if k != "status"}
    return Drafted("ok", {**doc, "design": design, "sections": secs})
