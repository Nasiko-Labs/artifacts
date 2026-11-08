"""One analysis: scrub -> cluster (code) -> [merge (1 call)] -> draft (<= top calls) -> check (code) -> Stored.

Zero model calls before the merge/draft steps. What is kept for follow-ups is capped so the checkpoint stays
small: groups of 2+ replies (the top STORE_MAX_GROUPS), up to 30 member texts each, each cut to 600 chars."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

import merge as merge_step
from candidates import Group, build_groups, rank_key, select
from draft import Job, draft_all
from llm import TurnDeps
from parse_replies import Reply
from prepare import Prepared, cluster_replies, macro_used, prepare
from report import Stored

STORE_MAX_GROUPS = 60
STORE_TEXT_CHARS = 600


def _jobs(groups: Sequence[Group], prepared: Prepared, doc_clusters: dict[str, Sequence[int]]) -> list[Job]:
    by_id = {r.id: r.text for r in prepared.replies}
    jobs = []
    for group in groups:
        members = tuple(prepared.replies[prepared.doc_reply[d]].text for d in doc_clusters[group.key])
        jobs.append(Job(group.key, tuple((rid, by_id[rid]) for rid in group.sample), members))
    return jobs


def _kept(groups: Sequence[Group], has_tickets: bool, must: set[str]) -> tuple[Group, ...]:
    ranked = sorted((g for g in groups if g.frequency >= 2), key=lambda g: rank_key(g, has_tickets))
    kept = ranked[:STORE_MAX_GROUPS]
    return tuple(kept + [g for g in ranked[STORE_MAX_GROUPS:] if g.key in must])


async def analyse(deps: TurnDeps, replies: Sequence[Reply], macros: Sequence[str], min_freq: int, top: int,
                  context: Sequence[str], notes: Sequence[str] = (), extras: Mapping | None = None) -> Stored | None:
    """The stored result, or None when no reply is long enough to compare. `extras` sets the live-only
    Stored fields (ticket urls, the live confidence cap, the headline)."""
    prepared = prepare(replies, macros)
    if not prepared.docs:
        return None
    clusters, merged = await merge_step.merge(deps, cluster_replies(prepared), prepared, min_freq)
    groups = build_groups(prepared, clusters, macros)
    doc_clusters = {g.key: c for g, c in zip(groups, clusters, strict=True)}
    has_agents = any(r.agent for r in prepared.replies)
    has_tickets = any(r.ticket for r in prepared.replies)
    selection = select(groups, min_freq, top, has_agents, has_tickets)
    chosen = [g for g, _ in selection.candidates]
    drafts = await draft_all(deps, _jobs(chosen, prepared, doc_clusters), prepared.names, context) if chosen else {}
    kept = _kept(groups, has_tickets, {g.key for g in chosen} | {g.key for g in selection.covered})
    wanted = {rid for g in kept for rid in (*g.members, *g.sample)}
    texts = {r.id: r.text[:STORE_TEXT_CHARS] for r in prepared.replies if r.id in wanted}
    tickets = {r.id: r.ticket for r in prepared.replies if r.id in wanted and r.ticket}
    singletons = sum(1 for c in clusters if len(c) == 1)
    stored = Stored(total=len(prepared.replies), too_short=prepared.too_short,
                    unclustered=prepared.too_short + singletons, duplicates=prepared.duplicates, merged=merged,
                    boilerplate=prepared.boilerplate, groups=kept, texts=texts, tickets=tickets, drafts=drafts,
                    min_freq=min_freq, top=top, has_agents=has_agents, has_tickets=has_tickets, notes=tuple(notes),
                    macro_used=macro_used(prepared, macros) if macros else None)
    return replace(stored, **dict(extras or {}))
