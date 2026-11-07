"""Drafting: one call writes statements over the usable evidence; code verifies each one; one repair call fixes
the language failures; what still fails is dropped and counted."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import prompts
import verify
from chunking import coverage_text, draft_block
from evidence import Evidence
from llm import (
    OPTIONAL_CALL_RESERVE_SECS,
    LlmError,
    TurnDeps,
    call,
    complete_json,
    load,
)

log = logging.getLogger("performance-review-drafter.draft")

CAPS = {"standard": {"strengths": 6, "growth": 4, "summary": 5}, "short": {"strengths": 3, "growth": 2, "summary": 2}}
DRAFT_MAX_TOKENS = 2_500


@dataclass(frozen=True)
class Drafted:
    status: str                                  # ok | off_topic | failed
    statements: tuple[verify.Statement, ...] = ()
    dropped: tuple[dict, ...] = ()               # {text, reason}
    repaired: int = 0


@dataclass(frozen=True)
class Request:
    usable: tuple[Evidence, ...]
    rows: dict
    ctx: verify.Context
    names: dict[str, str]
    person: str
    pronouns: str = ""
    length: str = "standard"


def _validate(data: Any) -> dict | None:
    if not isinstance(data, dict):
        return None
    if data.get("status") == "off_topic":
        return {"status": "off_topic", "statements": []}
    statements = data.get("statements")
    return {"status": "ok", "statements": statements} if isinstance(statements, list) else None


def _counts(length: str) -> str:
    caps = CAPS.get(length, CAPS["standard"])
    return (f"up to {caps['strengths']} strengths, up to {caps['growth']} growth areas, a summary of 2 to "
            f"{caps['summary']} sentences (one statement each)")


async def draft(deps: TurnDeps, req: Request, policy: Sequence[str] = ()) -> Drafted:
    messages = prompts.draft_messages(draft_block(req.usable), coverage_text(req.rows, req.ctx.gaps), req.person,
                                      req.pronouns, _counts(req.length), req.names, policy)
    try:
        data = await complete_json(deps, messages, prompts.DRAFT_CONTRACT, _validate, DRAFT_MAX_TOKENS)
    except LlmError as e:
        log.warning("draft failed: %s", e)
        return Drafted("failed")
    if data["status"] == "off_topic":
        return Drafted("off_topic")
    return await _checked(deps, req, data["statements"], policy)


async def _checked(deps: TurnDeps, req: Request, raws: list, policy: Sequence[str]) -> Drafted:
    kept: list[verify.Statement] = []
    fixable: list[tuple[verify.Statement, tuple[str, ...]]] = []
    dropped: list[dict] = []
    for raw in raws:
        statement = verify.from_raw(raw)
        if statement is None:
            continue
        verdict = verify.check(statement, req.ctx)
        if verdict.kind == verify.OK:
            kept.append(verdict.statement)
        elif verdict.kind == verify.REPAIR:
            fixable.append((verdict.statement, verdict.reasons))
        else:
            dropped.append({"text": statement.text, "reason": verdict.reasons[0]})
    fixed, still = await _repair(deps, req, fixable, policy) if fixable else ([], [])
    final = select([*kept, *fixed], req.length)
    return Drafted("ok", tuple(final), (*dropped, *still), len(fixed))


async def _repair(deps: TurnDeps, req: Request, fixable: list, policy: Sequence[str]) -> tuple[list, list]:
    failing = [{"id": f"s{n}", **s.to_dict(), "reasons": list(r)} for n, (s, r) in enumerate(fixable, start=1)]
    lost = [{"text": s.text, "reason": r[0]} for s, r in fixable]
    if not deps.has_time(OPTIONAL_CALL_RESERVE_SECS) or not deps.budget.take_repair():
        return [], lost
    cited = {i for s, _ in fixable for i in s.evidence}
    block = draft_block([e for e in req.usable if e.id in cited])
    try:
        data = load(await call(deps, prompts.repair_messages(block, json.dumps(failing, ensure_ascii=False),
                                                             req.person, req.pronouns, policy), DRAFT_MAX_TOKENS))
    except LlmError as e:
        log.warning("repair failed: %s", e)
        return [], lost
    raws = data.get("statements") if isinstance(data, dict) else None
    wanted = {f["id"]: n for n, f in enumerate(failing)}
    fixed: dict[int, verify.Statement] = {}
    for raw in raws or ():
        index = wanted.get(str(raw.get("id"))) if isinstance(raw, dict) else None
        statement = verify.from_raw(raw) if index is not None else None
        verdict = verify.check(statement, req.ctx) if statement else None
        if verdict and verdict.kind == verify.OK and index not in fixed:
            fixed[index] = verdict.statement
    still = [lost[n] for n in range(len(lost)) if n not in fixed]
    return [fixed[n] for n in sorted(fixed)], still


def select(statements: Sequence[verify.Statement], length: str) -> list[verify.Statement]:
    """Section caps in the model's order; exact duplicates once."""
    caps = CAPS.get(length, CAPS["standard"])
    taken: dict[str, int] = {}
    seen: set[str] = set()
    out: list[verify.Statement] = []
    for s in statements:
        key = verify.norm(s.text)
        if key in seen or taken.get(s.section, 0) >= caps[s.section]:
            continue
        seen.add(key)
        taken[s.section] = taken.get(s.section, 0) + 1
        out.append(s)
    return out


def shorter(statements: Sequence[verify.Statement]) -> list[verify.Statement]:
    """"shorter": the short caps, keeping the statements with the most evidence (ties in order)."""
    ranked = sorted(enumerate(statements), key=lambda p: (-len(p[1].evidence), p[0]))
    kept = select([s for _, s in ranked], "short")
    order = {id(s): n for n, s in enumerate(statements)}
    return sorted(kept, key=lambda s: order[id(s)])
