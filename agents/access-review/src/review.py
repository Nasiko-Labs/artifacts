"""One review: parse the pastes (code), read the code-host organization (tool loops), match identities,
apply the rules, draft removals (code), write the headline (one model call) and render. The stored
result holds findings, drafts and counts only: never the raw paste or the parsed rows."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import coverage
import drafts
import findings as fd
import intake
import narrate
import orgscan
import parse
import prompts
import render
import rules
from deps import TurnDeps
from identity import Roster
from parse import AccessRow, RosterRow
from policy import Policy
from reader import ask_once

log = logging.getLogger("access_review.review")


@dataclass(frozen=True)
class Parsed:
    access: tuple[AccessRow, ...]
    access_rejected: int
    roster: tuple[RosterRow, ...]
    roster_rejected: int
    tables: int


async def parse_request(deps: TurnDeps, request: intake.Request) -> Parsed:
    access_blocks, roster_blocks = list(request.access), list(request.roster)
    if request.unclassified:
        labels = await _classify(deps, request.unclassified)
        access_blocks += [b for b, lab in zip(request.unclassified, labels, strict=True) if lab == "access"]
        roster_blocks += [b for b, lab in zip(request.unclassified, labels, strict=True) if lab == "roster"]
    access, a_rej = _rows(access_blocks, request.envelope_access, parse.access_rows)
    roster, r_rej = _rows(roster_blocks, request.envelope_roster, parse.roster_rows)
    tables = len(access_blocks) + len(roster_blocks) + bool(request.envelope_access) + bool(request.envelope_roster)
    return Parsed(access, a_rej, roster, r_rej, tables)


def _rows(blocks: Sequence[str], records: tuple, reader) -> tuple[tuple, int]:
    rows, rejected = [], 0
    for block in blocks:
        got, bad = reader(parse.table(block.splitlines()))
        offset = len(rows)
        rows += [_renumber(r, offset) for r in got]
        rejected += bad
    if records:
        got, bad = reader(parse.Table((), (), 0), records)
        rows += list(got)
        rejected += bad
    return tuple(rows), rejected


def _renumber(row, offset: int):
    return row if not offset else type(row)(**{**row.__dict__, "row": row.row + offset})


async def _classify(deps: TurnDeps, blocks: Sequence[str]) -> list[str]:
    """Header rows only (never data) go to the model; anything unclear stays unused."""
    headers = [next((ln for ln in b.splitlines() if ln.strip()), "")[:300] for b in blocks]
    listing = "\n".join(f"{i}. {h}" for i, h in enumerate(headers, start=1))
    text = await ask_once(deps, ({"role": "system", "content": prompts.CLASSIFY_SYSTEM},
                                 {"role": "user", "content": prompts.fence_paste(listing)}))
    try:
        labels = json.loads(text.strip().strip("`").removeprefix("json").strip()).get("labels") or {}
    except (ValueError, AttributeError):
        labels = {}
    return [str(labels.get(str(i)) or "neither") if isinstance(labels, Mapping) else "neither"
            for i in range(1, len(blocks) + 1)]


async def run(deps: TurnDeps, request: intake.Request, parsed: Parsed, policy: Policy, mappings: Mapping[str, str],
              changed: Sequence[str] = ()) -> dict:
    """The graph update for a finished review: reply, stored review and envelope."""
    roster = Roster(parsed.roster)
    org = None
    notes: tuple[str, ...] = ()
    if request.org:
        scanned = await orgscan.scan(deps, request.org, request.repos, roster, mappings)
        org, notes = scanned.data, scanned.notes
    elif request.wants_code_host:
        notes = ("Code-host org not reviewed: no organization was named (say `org <name>`).",)
    result = rules.evaluate(parsed.access, roster, org, policy, deps.now.date(), mappings)
    removals = drafts.draft(result.findings)
    inputs = coverage.Inputs(len(parsed.access), parsed.access_rejected, len(parsed.roster), parsed.roster_rejected,
                             bool(request.org or request.wants_code_host), org is not None, result.holders,
                             result.matched, result.weak)
    conf = coverage.confidence(inputs)
    counts = fd.counts(result.findings)
    forbidden = [x for f in result.findings for x in (f.person, f.email, f.system)]
    headline = await narrate.headline(deps, result.holders, result.privileged, counts, len(removals), forbidden)
    stored = {
        "findings": [f.to_dict() for f in result.findings], "removals": [r.to_dict() for r in removals],
        "holders": result.holders, "privileged": result.privileged, "export_rows": len(parsed.access),
        "roster_rows": len(parsed.roster), "on_leave": result.on_leave, "policy": policy.to_dict(),
        "changed": list(changed), "headline": headline, "coverage": list(coverage.notes(inputs, notes)),
        "confidence": conf, "needs_human": coverage.needs_human(len(result.findings), conf),
        "systems": sorted({f.system for f in result.findings}),
        "org": _org_summary(org),
    }
    if not result.findings:
        stored = {**stored, "headline": prompts.NO_FINDINGS.format(holders=result.holders)}
    return {"reply": render.report(stored), "review": stored, "envelope": render.envelope(stored), "route": ""}


def _org_summary(org) -> dict:
    if org is None:
        return {}
    return {"name": org.org, "members": len(org.members), "owners": len(org.owners), "outside": len(org.outside),
            "teams": org.teams, "repos_checked": len(org.repos_checked)}
