"""The model calls of a turn. A fresh draft: extract (1 call per 20k chunk) -> design (1) -> sections (3, or
1 in ADR mode, concurrent) -> code checks -> at most one repair call. A revision: one REVISE call that
returns only the keys it changes. Adding requirements: one extract call, then one revision."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import checks
import design as design_mod
import extract as extract_mod
import prompts
import sections as sections_mod
from inputs import Request, is_agent_system
from llm import LlmError, TurnDeps, complete_json, off_topic

log = logging.getLogger("design-doc-drafter.pipeline")


@dataclass(frozen=True)
class Drafted:
    status: str                  # ok | off_topic | empty | failed
    doc: dict | None = None


def blocks(doc: dict) -> dict[str, str]:
    reqs = "\n".join(f"{r['id']} [{r['type']}] {r['text']}" for r in doc["reqs"])
    return {"REQUIREMENTS": reqs, "CONTEXT": "\n".join(doc.get("context") or ()),
            "GUIDANCE": doc.get("guidance") or "", "ANSWERS": "\n".join(doc.get("answers") or ())}


def ids_of(doc: dict) -> list[str]:
    return [r["id"] for r in doc["reqs"]]


async def draft(deps: TurnDeps, request: Request, policy: Sequence[str]) -> Drafted:
    try:
        ex = await extract_mod.extract(deps, request.requirements, policy)
        if ex.status == "off_topic":
            return Drafted("off_topic")
        if not ex.requirements:
            return Drafted("empty")
        doc = {"title": ex.title, "source": request.requirements, "context": list(request.context),
               "guidance": request.guidance, "answers": [], "reqs": list(ex.requirements),
               "agent": is_agent_system(request.requirements), "adr": request.adr, "short": request.short,
               "qnums": {}, "answered": [], "repaired": False, "dropped_requirements": ex.dropped,
               "skipped_chars": ex.skipped_chars}
        return await _design_and_write(deps, doc, policy)
    except LlmError as e:
        log.warning("draft failed: %s", e)
        return Drafted("failed")


async def _design_and_write(deps: TurnDeps, doc: dict, policy: Sequence[str]) -> Drafted:
    ids, data = ids_of(doc), blocks(doc)
    proposal = await design_mod.propose(deps, data, ids, policy, doc["short"])
    if proposal["status"] == "off_topic":
        return Drafted("off_topic")
    design = {k: v for k, v in proposal.items() if k != "status"}
    keys = ["ADR"] if doc["adr"] else ["A", "B", "C"]
    data = {**data, "DESIGN": design_mod.as_block(design)}
    status, secs, failed = await sections_mod.write(deps, keys, data, ids, policy, doc["agent"], doc["short"])
    if status == "off_topic":
        return Drafted("off_topic")
    if failed == len(keys):
        return Drafted("failed")
    design, secs, repaired = await repair(deps, design, secs, doc, data)
    return Drafted("ok", {**doc, "design": design, "sections": secs, "repaired": repaired})


async def repair(deps: TurnDeps, design: dict, secs: dict, doc: dict, data: dict) -> tuple[dict, dict, bool]:
    """At most one call listing the exact gaps; whatever remains becomes an open question later."""
    reqs = [] if doc["adr"] else doc["reqs"]
    gaps = checks.structural_gaps(design, secs, reqs)
    if not gaps or not deps.has_time() or not deps.budget.take_repair():
        return design, secs, False
    draft_block = json.dumps({"acceptance_criteria": secs.get("acceptance_criteria") or []}, ensure_ascii=False)
    try:
        raw = await complete_json(deps, prompts.repair_messages(gaps, {**data, "DRAFT": draft_block}),
                                  prompts.REPAIR_CONTRACT, lambda d: d if isinstance(d, dict) else None)
    except LlmError as e:
        log.warning("repair failed: %s", e)
        return design, secs, True
    ids = ids_of(doc)
    fixed = {**design_mod.clean({k: raw[k] for k in ("flows", "contracts") if k in raw}, ids),
             "acceptance_criteria": sections_mod.clean_criteria(raw.get("acceptance_criteria"), ids)}
    new_design, new_secs = checks.apply_repair(design, secs, fixed)
    return new_design, new_secs, True


def _revise_validator(data: Any) -> dict | None:
    if off_topic(data):
        return {"status": "off_topic"}
    return data if isinstance(data, dict) else None


async def revise(deps: TurnDeps, doc: dict, instruction: str, policy: Sequence[str]) -> Drafted:
    """One call; only the keys it returns change. Requirements and their ids never change here."""
    current = {**doc["design"], **doc["sections"]}
    data = {**blocks(doc), "DRAFT": json.dumps(current, ensure_ascii=False)}
    try:
        raw = await complete_json(deps, prompts.revise_messages(instruction, data, policy),
                                  prompts.REVISE_CONTRACT, _revise_validator)
    except LlmError as e:
        log.warning("revision failed: %s", e)
        return Drafted("failed")
    if raw.get("status") == "off_topic":
        return Drafted("off_topic")
    ids = ids_of(doc)
    # A key that cleans to nothing is treated as unchanged: a revision never silently empties a section.
    changed_design = {k: v for k, v in design_mod.clean(raw, ids).items() if v}
    changed_secs = {k: v for k, v in sections_mod.clean(raw, ids).items() if v}
    if "acceptance_criteria" in changed_secs:
        changed_secs["acceptance_criteria"] = keep_coverage(doc["sections"].get("acceptance_criteria") or [],
                                                            changed_secs["acceptance_criteria"])
    design = {**doc["design"], **changed_design}
    secs = {**doc["sections"], **changed_secs}
    return Drafted("ok", {**doc, "design": design, "sections": secs})


def keep_coverage(old: Sequence[dict], new: Sequence[dict]) -> list[dict]:
    """The revised criteria, plus every old criterion for a requirement the revision no longer verifies."""
    verified = {i for c in new for i in c["verifies"]}
    return [*new, *(c for c in old if not set(c["verifies"]) <= verified)]


async def add_requirements(deps: TurnDeps, doc: dict, text: str, policy: Sequence[str]) -> Drafted:
    try:
        ex = await extract_mod.extract(deps, text, policy, start=len(doc["reqs"]) + 1)
    except LlmError as e:
        log.warning("extract failed: %s", e)
        return Drafted("failed")
    if ex.status == "off_topic":
        return Drafted("off_topic")
    known = {r["text"].casefold() for r in doc["reqs"]}
    fresh = [r for r in ex.requirements if r["text"].casefold() not in known]
    new = [{**r, "id": f"R{len(doc['reqs']) + n}"} for n, r in enumerate(fresh, start=1)]
    if not new:
        return Drafted("empty")
    grown = {**doc, "source": doc["source"] + "\n" + text, "reqs": [*doc["reqs"], *new],
             "dropped_requirements": doc.get("dropped_requirements", 0) + ex.dropped}
    added = ", ".join(r["id"] for r in new)
    instruction = (f"New requirements were added: {added}. Update components (their satisfies lists), flows, "
                   "contracts and acceptance criteria so they cover them.")
    return await revise(deps, grown, instruction, policy)
