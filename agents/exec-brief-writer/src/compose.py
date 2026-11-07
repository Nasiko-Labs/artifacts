"""Writing the brief: one compose call over the fenced sections (inputs up to SINGLE_PASS_CHARS), or
map-then-compose over checked candidates (larger inputs); at most one repair call either way. Every
answer goes through checks.check before it can be shown. Budget: single pass <= 2 calls, large
input <= 8 (6 map + compose + repair), a revision <= 2 (it reuses the stored candidates)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

import brief as b
import checks
import mapping
import prompts
from inputs import SINGLE_PASS_CHARS
from llm import Llm
from sections import Section, total_chars


@dataclass(frozen=True)
class Outcome:
    status: str  # ok | nothing_to_brief | off_topic | unreadable
    brief: b.Brief | None = None
    candidates: tuple[dict, ...] | None = None  # kept for revisions of a large input


@dataclass(frozen=True)
class Request:
    sections: tuple[Section, ...]
    instruction: str = ""
    org: tuple[str, ...] = ()
    prior: dict | None = None                    # the brief being revised
    candidates: tuple[dict, ...] | None = None   # stored map candidates, reused on a revision


def partial_note(done: int, total: int) -> str:
    return f"Briefed from {done} of {total} parts: time ran out before the rest were read."


async def write(llm: Llm, request: Request) -> Outcome:
    if request.candidates is None and total_chars(request.sections) <= SINGLE_PASS_CHARS:
        return await _single_pass(llm, request)
    candidates, note = request.candidates, None
    if candidates is None:
        mapped = await mapping.run(llm, request.sections)
        if mapped.done == 0:
            return Outcome("unreadable")
        candidates = mapped.candidates
        if mapped.done < mapped.total:
            note = partial_note(mapped.done, mapped.total)
    outcome = await _from_candidates(llm, request, candidates)
    if note and outcome.brief is not None:
        outcome = replace(outcome, brief=replace(outcome.brief, notes=(*outcome.brief.notes, note)))
    return outcome


async def _single_pass(llm: Llm, request: Request) -> Outcome:
    messages = ({"role": "system", "content": prompts.system(request.org)},
                {"role": "user", "content": prompts.compose_user(request.instruction, request.sections, request.prior)})
    return await _answer_and_repair(llm, messages, request.sections, b.parse)


async def _from_candidates(llm: Llm, request: Request, candidates: tuple[dict, ...]) -> Outcome:
    listing = [{k: v for k, v in c.items() if k != "cites"} | {"sections": sorted({x["section"] for x in c["cites"]})}
               for c in candidates]
    messages = ({"role": "system", "content": prompts.compose_candidates_system(request.org)},
                {"role": "user", "content": prompts.compose_candidates_user(request.instruction, listing, request.prior)})
    by_id = {c["id"]: c for c in candidates}
    outcome = await _answer_and_repair(llm, messages, request.sections, lambda data: from_choice(data, by_id))
    return replace(outcome, candidates=candidates)


async def _answer_and_repair(llm: Llm, messages: tuple[dict, ...], sections: Sequence[Section],
                             parse: Callable[[dict | None], b.Brief | None]) -> Outcome:
    data = await llm.json(messages)
    parsed = parse(data)
    checked = checks.check(parsed, sections) if parsed is not None and parsed.status != "off_topic" else None
    if parsed is not None and parsed.status == "off_topic":
        return Outcome("off_topic")
    problems = checked.problems if checked is not None else ("The reply was not the JSON object asked for.",)
    if problems and sections:  # nothing to repair against without sections (a scope check)
        retry = (*messages, {"role": "user", "content": prompts.repair_user(problems, data)})
        repaired = parse(await llm.json(retry))
        if repaired is not None and repaired.status == "off_topic" and checked is None:
            return Outcome("off_topic")
        if repaired is not None and repaired.status != "off_topic":
            checked = checks.check(repaired, sections)
    if checked is None:
        return Outcome("unreadable")
    return Outcome(checked.brief.status, checked.brief)


def from_choice(data: dict | None, by_id: dict[str, dict]) -> b.Brief | None:
    """The compose-from-candidates answer as a Brief: cites come from the chosen candidates; an
    unknown id is ignored."""
    if not isinstance(data, dict):
        return None
    status = str(data.get("status") or "").lower()
    if status not in b.STATUSES:
        return None
    if status == "off_topic":
        return b.Brief("off_topic")
    head = data.get("headline") if isinstance(data.get("headline"), dict) else {}
    head_ids = [i for i in head.get("from") or () if i in by_id]
    headline = b.Line(str(head.get("text") or ""), _cites(head_ids, by_id)) if head.get("text") else None
    risks = tuple(filter(None, (_chosen(r, by_id, "risk") for r in data.get("risks") or ())))
    decisions = tuple(filter(None, (_chosen(d, by_id, "decision") for d in data.get("decisions") or ())))
    return b.Brief(status, headline, risks, decisions)


def _cites(ids: Sequence[str], by_id: dict[str, dict]) -> tuple[b.Cite, ...]:
    return tuple(b.Cite(c["section"], c["snippet"]) for i in ids for c in by_id[i]["cites"])


def _chosen(row: object, by_id: dict[str, dict], kind: str) -> b.Line | None:
    if not isinstance(row, dict) or by_id.get(str(row.get("id")), {}).get("kind") != kind:
        return None
    candidate = by_id[str(row["id"])]
    text = " ".join(str(row.get("text") or candidate["text"]).split())
    line = b.Line(text, _cites([candidate["id"]], by_id))
    if kind == "risk":
        severity = str(row.get("severity") or candidate.get("severity") or "").lower()
        return replace(line, severity=severity if severity in b.SEVERITIES else b.DEFAULT_SEVERITY)
    return replace(line, owner=candidate.get("owner") or "", by=candidate.get("by") or "")
