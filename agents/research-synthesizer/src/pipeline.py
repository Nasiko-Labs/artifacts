"""One synthesis run: extract (map) -> cluster -> relate -> score -> unknowns -> compose. Returns the brief as a
plain dict (the checkpointed state the renderer and follow-ups read). `from_claims` re-runs everything after
extraction, which is how "add this source" works on the union of stored and new claims."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

import cluster as cluster_step
import compose
import confidence
import extract as extract_step
import independence
import relate as relate_step
import unknowns as unknowns_step
from chunking import Unit, plan_units, segments_of
from llm import TurnDeps
from model import Claim, Drops, Finding, Source


@dataclass(frozen=True)
class Outcome:
    status: str                  # ok | off_topic | failed | empty
    brief: dict | None = None


@dataclass(frozen=True)
class Reading:
    claims: tuple[Claim, ...]
    sources: tuple[Source, ...]
    drops: Drops
    timed_out: bool
    not_read: tuple[tuple[str, str], ...]     # (source id, reason)


async def run(deps: TurnDeps, question: str, sources: tuple[Source, ...], context: Sequence[str] = (),
              notes: tuple[str, ...] = (), prior: dict | None = None) -> Outcome:
    """A fresh brief, or (with `prior`) the prior brief plus these new sources."""
    old_claims = tuple(Claim.from_dict(c) for c in (prior or {}).get("claims", ()))
    units, over_cap = plan_units(sources)
    extracted = await extract_step.extract(deps, question, units, context, first_claim=len(old_claims) + 1)
    if extracted.off_topic and not old_claims:
        return Outcome("off_topic")
    if extracted.failed == len(units) and units and not old_claims:
        return Outcome("failed")
    reading = _reading(extracted, sources, over_cap)
    if prior:
        reading = _merge(prior, reading, old_claims)
    if not reading.claims:
        return Outcome("empty")
    return Outcome("ok", await from_claims(deps, question, reading, context, notes))


def _reading(extracted: extract_step.Extracted, sources: tuple[Source, ...], over_cap: tuple[Unit, ...]) -> Reading:
    read = {(seg.source_id, seg.part) for u in extracted.read_units for seg in u.segments}
    timed = {seg.source_id for u in extracted.unread_units for seg in u.segments} if extracted.timed_out else set()
    capped = {seg.source_id for u in over_cap for seg in u.segments}
    marked, not_read = [], []
    for s in sources:
        parts = segments_of(s)
        done = sum((s.id, seg.part) in read for seg in parts)
        status = "full" if done == len(parts) else "partial" if done else "not_read"
        marked.append(replace(s, read=status))
        if status != "full":
            reason = "time limit" if s.id in timed else "call limit" if s.id in capped else "model call failed"
            not_read.append((s.id, reason))
    drops = Drops(quotes=extracted.dropped, irrelevant=extracted.irrelevant)
    return Reading(extracted.claims, tuple(marked), drops, extracted.timed_out, tuple(not_read))


def _merge(prior: dict, new: Reading, old_claims: tuple[Claim, ...]) -> Reading:
    old_sources = tuple(Source.from_dict(s) for s in prior.get("sources", ()))
    old_drops = Drops.from_dict(prior.get("drops") or {})
    drops = Drops(quotes=old_drops.quotes + new.drops.quotes, irrelevant=old_drops.irrelevant + new.drops.irrelevant)
    old_not_read = tuple(tuple(x) for x in prior.get("not_read", ()))
    return Reading(old_claims + new.claims, old_sources + new.sources, drops,
                   bool(prior.get("timed_out")) or new.timed_out, old_not_read + new.not_read)


async def from_claims(deps: TurnDeps, question: str, reading: Reading, context: Sequence[str],
                      notes: tuple[str, ...], extra_unknowns: tuple[str, ...] = ()) -> dict:
    claims = reading.claims
    by_id = {c.id: c for c in claims}
    clustered = await cluster_step.cluster(deps, question, claims, context)
    raw = [Finding(f"F{n}", grp.statement, grp.claim_ids) for n, grp in enumerate(clustered.groups, start=1)]
    related = await relate_step.relate(deps, [(f.id, [by_id[i] for i in f.claim_ids]) for f in raw], context)
    grouping = independence.groups(reading.sources)
    findings = [confidence.score(f, [by_id[i] for i in f.claim_ids], related.by_finding.get(f.id), grouping)
                for f in raw]
    findings.sort(key=lambda f: ({"high": 0, "medium": 1, "low": 2}[f.confidence], int(f.id[1:])))
    subs = await unknowns_step.sub_questions(deps, question, context)
    open_items = [*unknowns_step.unanswered(subs, findings),
                  *unknowns_step.code_gaps(question, findings, claims, reading.sources, deps.today()),
                  *extra_unknowns]
    line = await compose.bottom_line(deps, question, findings, claims, context)
    read_sources = [s for s in reading.sources if s.read != "not_read"]
    level = confidence.overall(findings, independence.independent_count((s.id for s in read_sources), grouping),
                               reading.timed_out, clustered.unclustered, len(claims))
    drops = replace(reading.drops, unclustered=clustered.unclustered, empty_findings=clustered.empty,
                    relations=related.rejected)
    return {
        "question": question, "sources": [s.to_dict() for s in reading.sources],
        "claims": [c.to_dict() for c in claims], "findings": [f.to_dict() for f in findings],
        "unknowns": list(dict.fromkeys(open_items)), "extra_unknowns": list(extra_unknowns), "bottom_line": line,
        "confidence": level, "needs_human": confidence.needs_human(level, findings, by_id, reading.timed_out),
        "drops": drops.to_dict(), "timed_out": reading.timed_out, "not_read": [list(x) for x in reading.not_read],
        "notes": list(notes), "cluster_fallback": clustered.fallback,
    }
