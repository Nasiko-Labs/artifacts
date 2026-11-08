"""One brief, end to end: collect (HN and the code host concurrently, plus pasted items) → store → cluster
(1 LLM call) → write (1 call) → verify (≤1 repair call) → score → envelope → render. Also the stored-data
rewrites a follow-up needs (refine, drill) without fetching again."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import coverage
import models
import render
import scoring
import sources_codehost
import sources_hn
import store
import write as writing
from cluster import Cluster, cluster
from deps import TurnDeps
from inputs import Request
from llm import LlmError
from pasted import Pasted
from prompts import FAILED_MODEL
from store import Item, Store
from verify import verify

log = logging.getLogger("opportunity_brief.brief")


@dataclass(frozen=True)
class Spec:
    """What to brief on; kept in state so "refresh" can run it again."""

    topic: str
    topic_stated: bool
    window_days: int
    angle: str
    repos: tuple[str, ...] = ()
    wants_hn: bool = False
    wants_code: bool = False
    notes: tuple[str, ...] = ()            # window note, not-read URLs, paste cuts
    hn_ids: tuple[str, ...] = ()
    pasted: tuple[Item, ...] = ()

    def to_dict(self) -> dict:
        return {"topic": self.topic, "topic_stated": self.topic_stated, "window_days": self.window_days,
                "angle": self.angle, "repos": list(self.repos), "wants_hn": self.wants_hn,
                "wants_code": self.wants_code, "notes": list(self.notes), "hn_ids": list(self.hn_ids),
                "pasted": [i.to_dict() for i in self.pasted]}

    @staticmethod
    def from_dict(d: Mapping) -> Spec:
        return Spec(str(d["topic"]), bool(d["topic_stated"]), int(d["window_days"]), str(d["angle"]),
                    tuple(d.get("repos") or ()), bool(d.get("wants_hn")), bool(d.get("wants_code")),
                    tuple(d.get("notes") or ()), tuple(str(i) for i in d.get("hn_ids") or ()),
                    tuple(Item.from_dict(i) for i in d.get("pasted") or ()))


def spec_from(topic: str, topic_stated: bool, req: Request, pasted: Pasted) -> Spec:
    notes = [n for n in (req.window_note,) if n] + list(coverage.not_read_lines(pasted.not_read))
    if pasted.cut_items:
        notes.append(f"{pasted.cut_items} pasted item(s) were longer than 1,500 characters and were cut.")
    if pasted.truncated_chars:
        notes.append(f"I read the first 20,000 pasted characters; {pasted.truncated_chars:,} more were not read.")
    if not topic_stated:
        notes.append("No topic was named, so I didn't search Hacker News or the code host; this brief uses "
                     "your pasted items only.")
    return Spec(topic, topic_stated, req.window_days, req.angle, req.repos, req.wants_hn, req.wants_code,
                tuple(notes), pasted.hn_ids, pasted.items)


@dataclass(frozen=True)
class Outcome:
    reply: str
    envelope: dict | None = None
    state: dict = field(default_factory=dict)   # what follow-ups need


async def run(deps: TurnDeps, spec: Spec) -> Outcome:
    search_topic = spec.topic if spec.topic_stated else None
    hn, code = await asyncio.gather(sources_hn.collect(deps, search_topic, spec.window_days, spec.hn_ids),
                                    sources_codehost.collect(deps, search_topic, spec.repos if spec.topic_stated else ()))
    held = store.build([*hn.items, *code.items, *spec.pasted], spec.window_days, deps.now, scoring.age_days,
                       lambda i: scoring.weighted(i, spec.window_days, deps.now))
    notes = [*spec.notes, *hn.notes, *code.notes]
    if held.cut:
        notes.append(f"{held.cut} more items were left out (I keep the strongest 40).")
    read = tuple(s for s, ok in (("hn", hn.read), ("code", code.read), ("pasted", bool(spec.pasted))) if ok)
    run_info = models.Run(spec.topic, spec.window_days, len(held.items), read, tuple(notes), spec.topic_stated,
                          (spec.wants_hn and not hn.read) or (spec.wants_code and code.failed))
    if not held.items:
        return Outcome(render.no_signal(spec.topic, spec.window_days, notes))
    try:
        clusters = await cluster(deps, spec.topic, held.items, spec.window_days)
        if not clusters:
            return Outcome(render.no_signal(spec.topic, spec.window_days, notes))
        return await _write_and_verify(deps, spec, held, clusters, run_info)
    except LlmError as e:
        log.warning("brief failed at the model: %s", e)
        return Outcome(FAILED_MODEL)


def ordered(held: Store, clusters: Sequence[Cluster], window_days: int, now) -> list[tuple[int, Cluster, list[Item]]]:
    by_id = held.by_id()
    scored = sorted(clusters, key=lambda c: -scoring.theme_score([by_id[i] for i in c.item_ids], window_days, now)[0])
    return [(n, c, [by_id[i] for i in c.item_ids]) for n, c in enumerate(scored, start=1)]


async def _write_and_verify(deps: TurnDeps, spec: Spec, held: Store, clusters: Sequence[Cluster],
                            run_info: models.Run, instruction: str = "") -> Outcome:
    themes = ordered(held, clusters, spec.window_days, deps.now)
    prompt = writing.write_user(spec.topic, spec.angle, spec.window_days, themes, deps.now, instruction=instruction)
    drafts = writing.drafts_by_index(await writing.write(deps, spec.topic, spec.angle, spec.window_days, themes,
                                                         instruction))
    checked, _ = await verify(deps, drafts, themes, held.by_id(), prompt)
    final, weak = models.themes(checked, themes, held.by_id(), spec.window_days, deps.now)
    env = models.envelope(run_info, final, weak, models.dropped_ratio(checked))
    state = {"spec": spec.to_dict(), "items": [i.to_dict() for i in held.items],
             "clusters": [c.to_dict() for c in clusters], "run": _run_dict(run_info), "envelope": env}
    if not final:
        return Outcome(render.no_signal(spec.topic, spec.window_days, [*run_info.sources_missing,
                                        *(f"Weak signal: {w}" for w in weak)]), env, state)
    return Outcome(render.full(env), env, state)


def _run_dict(r: models.Run) -> dict:
    return {"topic": r.topic, "window_days": r.window_days, "items": r.items, "sources_read": list(r.sources_read),
            "sources_missing": list(r.sources_missing), "topic_stated": r.topic_stated, "asked_failed": r.asked_failed}


def restore(state: Mapping) -> tuple[Spec, Store, list[Cluster], models.Run]:
    held = Store(tuple(Item.from_dict(i) for i in state["items"]))
    r = state["run"]
    run_info = models.Run(r["topic"], int(r["window_days"]), int(r["items"]), tuple(r["sources_read"]),
                          tuple(r["sources_missing"]), bool(r["topic_stated"]), bool(r["asked_failed"]))
    return Spec.from_dict(state["spec"]), held, [Cluster.from_dict(c) for c in state["clusters"]], run_info


async def refine(deps: TurnDeps, state: Mapping, instruction: str) -> Outcome:
    spec, held, clusters, run_info = restore(state)
    try:
        return await _write_and_verify(deps, spec, held, clusters, run_info, instruction)
    except LlmError as e:
        log.warning("refine failed at the model: %s", e)
        return Outcome(FAILED_MODEL)
