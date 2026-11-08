"""One comparison turn: parse the inputs (code) → fetch IaC + history from the code host → compare (code)
→ match audit events (code) → rank (code) → explain (one model call) → render (code).

Form A needs a pasted drift report (the repository is optional: without it file, line and code author
are unknown). Form B needs a live paste and the IaC file, fetched by path or pasted in a `=== path ===`
block; a live paste without any path ends the turn with one question ("Which IaC file defines this?").
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

import analyse
import audit
import envelope
import parse_drift
import prompts
import render
from analyse import Analysis, Sources
from deps import TurnDeps
from drift import Drift
from explain import explain
from fetch import Fetched, fetch, norm
from inputs import Inputs

log = logging.getLogger("drift.turn")


@dataclass(frozen=True)
class Outcome:
    reply: str
    data: dict | None = None
    report: dict | None = None   # kept in the checkpoint for follow-ups
    ask_path: bool = False


async def run(deps: TurnDeps, inp: Inputs, wants_json: bool = False) -> Outcome:
    if inp.too_long:
        return Outcome(prompts.TOO_LONG.format(limit=100_000))
    events = audit.parse(inp.audit_text)
    if inp.form == "A":
        return await _form_a(deps, inp, events, wants_json)
    if inp.form == "B":
        return await _form_b(deps, inp, events, wants_json)
    if inp.path:
        return Outcome(prompts.NEED_LIVE.format(path=inp.path))
    return Outcome(prompts.NOTHING_TO_COMPARE)


async def _form_a(deps: TurnDeps, inp: Inputs, events, wants_json: bool) -> Outcome:
    report = parse_drift.parse(inp.plan_text, inp.unchanged_code)
    if report.planned_only:
        return Outcome(prompts.PLAN_NOT_REFRESH)
    if not report.changes and report.unreadable:
        return Outcome(prompts.UNREADABLE_REPORT)
    wanted = tuple(c.address for c in report.changes if not c.address.startswith("module."))
    blocks = tuple(_block_text(a) for a in wanted[:analyse.MAX_RESOURCES])
    if inp.iac_blocks:
        fetched = Fetched(Sources(dict(inp.iac_blocks), {}, "pasted"))
    elif report.changes:
        fetched = await fetch(deps, inp.repo, inp.ref, (inp.path,) if inp.path else (), blocks)
    else:
        fetched = Fetched(Sources({}, {}, "none"))
    analysis = analyse.form_a(report, fetched.sources, events, deps.now, wanted)
    extra = (*fetched.notes, *(f"Couldn't read the change block for {a}." for a in report.unreadable))
    return await finish(deps, inp, analysis, extra, fetched.gaps, wants_json)


def _block_text(address: str) -> str:
    parts = address.split(".")
    if parts[0] == "data" and len(parts) >= 3:
        return f'data "{parts[1]}" "{parts[2].split("[")[0]}"'
    return f'resource "{parts[0]}" "{parts[1].split("[")[0]}"' if len(parts) >= 2 else address


async def _form_b(deps: TurnDeps, inp: Inputs, events, wants_json: bool) -> Outcome:
    notes: tuple[str, ...] = ()
    gaps = False
    if inp.iac_blocks:
        path, text = inp.iac_blocks[0]
        sources = Sources({path: text}, {}, "pasted")
        notes = ("The IaC was pasted, not read from the code host, so its last code author is unknown.",)
    elif not inp.path:
        return Outcome(prompts.ASK_PATH, ask_path=True)
    elif not inp.repo:
        return Outcome(prompts.ASK_REPO.format(path=inp.path))
    else:
        fetched = await fetch(deps, inp.repo, inp.ref, (inp.path,), ())
        path, text = inp.path, fetched.sources.files.get(norm(inp.path), "")
        if not text:
            return Outcome("\n\n".join([prompts.NEED_IAC.format(path=inp.path), *fetched.notes]))
        sources, notes, gaps = fetched.sources, fetched.notes, fetched.gaps
    analysis = analyse.form_b(path, text, inp.live_text, sources, events, deps.now)
    if isinstance(analysis, str):
        return Outcome(analysis)
    return await finish(deps, inp, analysis, notes, gaps, wants_json)


async def finish(deps: TurnDeps, inp: Inputs, a: Analysis, extra_notes, gaps: bool, wants_json: bool) -> Outcome:
    repo = inp.repo
    summary = envelope.summary(a, a.drifts, repo)
    drifts, headline = await explain(deps, a.drifts, summary)
    a = replace(a, drifts=drifts)
    notes = envelope.coverage_notes(a, inp.show_extras, extra_notes)
    state = to_state(a, repo=repo, headline=headline, notes=notes, gaps=gaps)
    return present(a, a.drifts, state, show_extras=inp.show_extras, wants_json=wants_json)


def present(a: Analysis, drifts, state: dict, *, show_extras: bool, wants_json: bool, lead: str = "",
            detail: bool = False) -> Outcome:
    env = envelope.build(a, drifts, repo=state["repo"], headline=state["headline"], notes=state["notes"],
                         show_extras=show_extras, fetch_gaps=state["gaps"])
    if wants_json:
        return Outcome(render.as_json(env), env, state)
    text = render.report(drifts, repo=state["repo"], unresolved=a.unresolved, compared=a.compared,
                         headline=state["headline"], notes=state["notes"],
                         extras=a.extras if show_extras else (), detail=detail)
    return Outcome(f"{lead}\n\n{text}" if lead else text, env, state)


def to_state(a: Analysis, *, repo: str, headline: str, notes, gaps: bool) -> dict:
    return {"form": a.form, "drifts": [d.to_state() for d in a.drifts], "extras": [d.to_state() for d in a.extras],
            "compared": a.compared, "unresolved": a.unresolved, "resources": a.resources,
            "iac_source": a.iac_source, "unlocated": list(a.unlocated), "capped": list(a.capped),
            "audit_given": a.audit_given, "repo": repo, "headline": headline, "notes": list(notes), "gaps": gaps}


def from_state(state: dict) -> Analysis:
    return Analysis(
        form=str(state.get("form") or ""), drifts=tuple(Drift.from_state(d) for d in state.get("drifts") or ()),
        extras=tuple(Drift.from_state(d) for d in state.get("extras") or ()), compared=int(state.get("compared") or 0),
        unresolved=int(state.get("unresolved") or 0), resources=int(state.get("resources") or 0),
        iac_source=str(state.get("iac_source") or "none"), unlocated=tuple(state.get("unlocated") or ()),
        capped=tuple(state.get("capped") or ()), audit_given=bool(state.get("audit_given")))
