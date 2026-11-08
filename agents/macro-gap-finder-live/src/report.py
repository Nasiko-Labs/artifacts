"""Pure: the stored result (checkpointed per conversation) and the output envelope built from it.

Every number (frequency, share, agents, tickets, coverage) is computed here or earlier in code, never by the
model. `confidence` and `needs_human` are computed from the run's checks."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import grounding
from candidates import Group, Selection, select
from draft import Draft, Job, fallback

HIGH_MIN_REPLIES = 100
LOW_MAX_REPLIES = 30
LOW_UNCLUSTERED_SHARE = 0.30
EXAMPLE_URLS = 3
LEVELS = ("high", "medium", "low")
NOT_DRAFTED = "not drafted in this run; ask for a redraft of it"


@dataclass(frozen=True)
class Stored:
    total: int
    too_short: int = 0
    unclustered: int = 0
    duplicates: int = 0
    merged: int = 0
    boilerplate: tuple[tuple[str, float], ...] = ()
    groups: tuple[Group, ...] = ()
    texts: dict = field(default_factory=dict)      # reply id -> scrubbed text (members of stored groups)
    tickets: dict = field(default_factory=dict)    # reply id -> ticket id
    drafts: dict = field(default_factory=dict)     # group key -> Draft
    min_freq: int = 3
    top: int = 8
    has_agents: bool = False
    has_tickets: bool = False
    notes: tuple[str, ...] = ()
    macro_used: int | None = None                  # replies >= 0.8 Jaccard to an existing macro (informational)
    urls: dict = field(default_factory=dict)       # live: ticket id -> ticket url seen in tool output
    cap_level: str = ""                            # live: "", "medium" or "low" (the worse level wins)
    headline: str = ""                             # live: what was read ("... on 60 solved tickets ...")

    def to_state(self) -> dict:
        return {**self.__dict__, "boilerplate": [list(b) for b in self.boilerplate],
                "groups": [g.to_dict() for g in self.groups], "drafts": {k: d.to_dict() for k, d in self.drafts.items()},
                "notes": list(self.notes)}

    @staticmethod
    def from_state(data: dict | None) -> Stored | None:
        if not data:
            return None
        return Stored(**{**data, "boilerplate": tuple((str(s), float(v)) for s, v in data.get("boilerplate") or ()),
                         "groups": tuple(Group.from_dict(g) for g in data.get("groups") or ()),
                         "drafts": {k: Draft.from_dict(d) for k, d in (data.get("drafts") or {}).items()},
                         "notes": tuple(data.get("notes") or ())})

    def selection(self) -> Selection:
        return select(self.groups, self.min_freq, self.top, self.has_agents, self.has_tickets)

    def job(self, group: Group) -> Job:
        sample = tuple((rid, self.texts[rid]) for rid in group.sample if rid in self.texts)
        return Job(group.key, sample, tuple(self.texts[m] for m in group.members if m in self.texts))

    def draft_for(self, group: Group) -> Draft:
        return self.drafts.get(group.key) or fallback(self.job(group), (), NOT_DRAFTED)

    def with_draft(self, key: str, draft: Draft) -> Stored:
        return replace(self, drafts={**self.drafts, key: draft})


def share(frequency: int, total: int) -> float:
    return round(frequency / total * 100, 1) if total else 0.0


def candidate(n: int, group: Group, draft: Draft, note: str, stored: Stored) -> dict:
    entry = {"id": n, "pattern": draft.pattern, "frequency": group.frequency,
             "share": share(group.frequency, stored.total), "draft": draft.text, "grounded": draft.grounded,
             "verbatim_fallback": draft.fallback, "sample_ticket_ids": list(group.ticket_ids)}
    if stored.has_agents:
        entry["distinct_agents"] = group.agents
    if stored.has_tickets:
        entry["distinct_tickets"] = group.tickets
    if stored.urls:
        entry["example_urls"] = [stored.urls[t] for t in group.ticket_ids if t in stored.urls][:EXAMPLE_URLS]
    notes = [x for x in (note, draft.note) if x]
    if notes:
        entry["note"] = "; ".join(notes)
    return entry


def confidence(stored: Stored, drafts: list[Draft]) -> str:
    """The method's level, lowered by the live caps (a source not read, a cap hit, the salvage)."""
    if (stored.total < LOW_MAX_REPLIES or stored.unclustered / max(stored.total, 1) > LOW_UNCLUSTERED_SHARE
            or any(d.fallback for d in drafts)):
        method = "low"
    else:
        method = "high" if stored.total >= HIGH_MIN_REPLIES else "medium"
    return max(method, stored.cap_level or "high", key=LEVELS.index)


def caveats(stored: Stored, selection: Selection) -> list[str]:
    found = list(stored.notes)
    if stored.too_short:
        found.append(f"{stored.too_short} replies were too short to compare.")
    if stored.duplicates:
        found.append(f"{stored.duplicates} replies were exact duplicates of another reply (counted as frequency).")
    if stored.merged:
        found.append(f"{stored.merged} groups of similar clusters were merged (checked in code).")
    if selection.beyond_top:
        found.append(f"{selection.beyond_top} more recurring answers are below the top {stored.top}.")
    return found


def envelope(stored: Stored) -> dict:
    selection = stored.selection()
    drafts = [stored.draft_for(g) for g, _ in selection.candidates]
    items = [candidate(n, g, d, note, stored)
             for n, ((g, note), d) in enumerate(zip(selection.candidates, drafts, strict=True), start=1)]
    covered = [{"pattern": grounding.fallback_pattern(stored.texts.get(g.key, "")), "frequency": g.frequency,
                "share": share(g.frequency, stored.total), "macro": g.covered} for g in selection.covered]
    covered_share = round(sum(i["frequency"] for i in items) / stored.total * 100) if stored.total else 0
    if items:
        summary = (f"{stored.total:,} replies analysed; {len(items)} recurring answers cover {covered_share}% of "
                   f"them; top: '{items[0]['pattern']}' ({items[0]['frequency']} replies)")
    else:
        summary = f"{stored.total:,} replies analysed; no answer recurs at least {stored.min_freq} times."
    level = confidence(stored, drafts)
    risky = level == "low" or any(d.fallback or grounding.mentions_money_or_time(d.text) for d in drafts)
    return {"summary": summary, "items": items, "macro_candidates": items, "covered_by_existing": covered,
            "boilerplate_ignored": [{"text": s, "share": round(v * 100, 1)} for s, v in stored.boilerplate],
            "caveats": caveats(stored, selection), "confidence": level, "needs_human": risky,
            "replies_analysed": stored.total, "min_frequency": stored.min_freq, "top": stored.top,
            "coverage_percent": covered_share,
            **({"macro_used": stored.macro_used} if stored.macro_used is not None else {})}
