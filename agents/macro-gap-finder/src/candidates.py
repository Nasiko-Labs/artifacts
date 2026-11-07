"""Pure: clusters -> groups with their counts, then the candidate rules, exclusion and ranking (all code).

- A group is a candidate when frequency >= min, and distinct tickets >= min when ticket ids are given, and
  distinct agents >= 2 when agent labels are given. One agent repeating themselves is still shown, labeled
  a single-agent habit, when frequency >= min + 2.
- A group whose centroid matches an existing macro (cosine >= 0.60) is "already covered", never a candidate.
- Ranking: distinct tickets (when given), frequency, distinct agents (desc), medoid length (asc), input order.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import cluster as clustering
from prepare import Prepared, covered_by

MIN_DEFAULT, MIN_RANGE = 3, (2, 20)
TOP_DEFAULT, TOP_RANGE = 8, (1, 10)
SINGLE_AGENT_EXTRA = 2
DRAFT_SAMPLE = 6
STORE_MAX_MEMBERS = 30
MACRO_PREVIEW_CHARS = 60
SINGLE_AGENT_NOTE = "single-agent habit: one agent writes this repeatedly (a macro helps them too)"


@dataclass(frozen=True)
class Group:
    key: str                          # the medoid reply's id
    members: tuple[str, ...]          # reply ids, most similar to the medoid first (capped for storage)
    frequency: int
    agents: int
    tickets: int
    ticket_ids: tuple[str, ...]
    medoid_tokens: int
    sample: tuple[str, ...]           # the medoid + up to 5 nearest, distinct agents preferred
    covered: str = ""                 # the existing macro it matches (first 60 chars)
    order: int = 0

    def to_dict(self) -> dict:
        return {**self.__dict__, "members": list(self.members), "ticket_ids": list(self.ticket_ids),
                "sample": list(self.sample)}

    @staticmethod
    def from_dict(data: dict) -> Group:
        return Group(key=str(data["key"]), members=tuple(data.get("members") or ()),
                     frequency=int(data.get("frequency") or 0), agents=int(data.get("agents") or 0),
                     tickets=int(data.get("tickets") or 0), ticket_ids=tuple(data.get("ticket_ids") or ()),
                     medoid_tokens=int(data.get("medoid_tokens") or 0), sample=tuple(data.get("sample") or ()),
                     covered=str(data.get("covered") or ""), order=int(data.get("order") or 0))


@dataclass(frozen=True)
class Selection:
    candidates: tuple[tuple[Group, str], ...]    # (group, note), ranked, cut to `top`
    covered: tuple[Group, ...]
    beyond_top: int


def clamp(value: int | None, default: int, bounds: tuple[int, int]) -> int:
    return default if value is None else max(bounds[0], min(bounds[1], int(value)))


def _sample(ordered: Sequence[int], prepared: Prepared) -> tuple[str, ...]:
    """The medoid, then the nearest members from agents not yet used, then the nearest others."""
    replies = prepared.replies
    picked, agents = [ordered[0]], {replies[ordered[0]].agent}
    for pass_new_agents in (True, False):
        for i in ordered[1:]:
            if len(picked) >= DRAFT_SAMPLE:
                break
            fresh = replies[i].agent not in agents
            if i not in picked and (fresh or not pass_new_agents or not replies[i].agent):
                picked.append(i)
                agents.add(replies[i].agent)
    return tuple(replies[i].id for i in picked)


def build_groups(prepared: Prepared, doc_clusters: Sequence[Sequence[int]], macros: Sequence[str]) -> list[Group]:
    groups = []
    for docs in doc_clusters:
        center = clustering.medoid(docs, prepared.docs)
        ordered_docs = [center, *clustering.nearest(docs, center, prepared.docs, len(docs))]
        ordered = [prepared.doc_reply[d] for d in ordered_docs]
        members = [prepared.replies[i] for i in ordered]
        tickets = list(dict.fromkeys(r.ticket for r in sorted(members, key=lambda r: int(r.id[1:])) if r.ticket))
        macro = covered_by(prepared, docs)
        groups.append(Group(
            key=members[0].id, members=tuple(r.id for r in members[:STORE_MAX_MEMBERS]), frequency=len(members),
            agents=len({r.agent.casefold() for r in members if r.agent}), tickets=len(tickets),
            ticket_ids=tuple(tickets[:3]), medoid_tokens=prepared.docs[center].token_count,
            sample=_sample(ordered, prepared), order=min(ordered),
            covered=" ".join(macros[macro].split())[:MACRO_PREVIEW_CHARS] if macro is not None else ""))
    return groups


def rank_key(group: Group, has_tickets: bool) -> tuple:
    return (-(group.tickets if has_tickets else 0), -group.frequency, -group.agents, group.medoid_tokens, group.order)


def select(groups: Sequence[Group], min_freq: int, top: int, has_agents: bool, has_tickets: bool) -> Selection:
    chosen, covered = [], []
    for group in groups:
        if group.frequency < min_freq or (has_tickets and group.tickets < min_freq):
            continue
        if group.covered:
            covered.append(group)
            continue
        single = has_agents and group.agents < 2
        if single and group.frequency < min_freq + SINGLE_AGENT_EXTRA:
            continue
        chosen.append((group, SINGLE_AGENT_NOTE if single else ""))
    chosen.sort(key=lambda pair: rank_key(pair[0], has_tickets))
    covered.sort(key=lambda g: rank_key(g, has_tickets))
    return Selection(tuple(chosen[:top]), tuple(covered), max(0, len(chosen) - top))
