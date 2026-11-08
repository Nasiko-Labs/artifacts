"""Graph nodes, output side: render (envelope + reply, all code) and followup (answers about the last
localization from stored state: zero tool calls unless the user names a new path, zero model calls)."""

from __future__ import annotations

import checks
import coverage
import followup as fu
import prompts
import render
import scoring
import triage
from candidates import Candidate
from inputs import PastedFile, Request
from skeleton_types import Symbol
from turn_read import Scope, issue_doc, repo_of


def envelope(state: dict, scope: Scope) -> dict:
    issue = issue_doc(state["issue"])
    kind = state.get("issue_kind") or "unclear"
    known = checks.Known(state.get("listing") or [],
                         [s["name"] for syms in (state.get("skeletons") or {}).values() for s in syms], issue.text())
    picks = state.get("picks") or []
    ranks = {p: i + 1 for i, (p, _) in enumerate(picks)}
    reasons = {p: r for p, r in picks}
    files = state.get("files") or {}
    sks = state.get("skeletons") or {}
    suspects = [{"path": s.path, "score": s.score, "bucket": s.bucket,
                 "why": checks.reason(reasons.get(s.path, ""), known, scope.dropped),
                 "signals": list(s.signals) + (["model pick"] if s.model_rank else []),
                 "symbols": [n for p, n, _ in state.get("symbols") or [] if p == s.path],
                 "truncated": bool(files.get(s.path, {}).get("truncated"))}
                for s in scoring.suspects([Candidate.from_dict(c) for c in state.get("candidates") or []], ranks)]
    order = {s["path"]: i for i, s in enumerate(suspects)}
    locations = sorted(state.get("locations") or [], key=lambda x: order.get(x["path"], 99))
    by_name = {(p, s["name"]): s for p, syms in sks.items() for s in syms}
    symbols = [{"path": p, "name": n, "kind": by_name[(p, n)]["kind"], "start_line": by_name[(p, n)]["start"],
                "end_line": by_name[(p, n)]["end"], "why": checks.reason(r, known, scope.dropped)}
               for p, n, r in state.get("symbols") or [] if (p, n) in by_name]
    missing = triage.missing_info(issue.title, issue.text(), kind)
    info_needed = triage.needs_info(kind, missing)
    top_loc = next((x for x in locations if suspects and x["path"] == suspects[0]["path"]), None)
    top = ({"path": suspects[0]["path"], **({"symbol": top_loc["symbol"], "start_line": top_loc["start_line"],
                                             "end_line": top_loc["end_line"]} if top_loc else {})}
           if suspects else None)
    has_search = scope.catalog is not None and scope.catalog.tool("search") is not None
    searched = has_search and scope.host is not None and scope.host.coverage.searches > 0
    pasted_count = len(state.get("listing") or []) if state.get("pasted_only") else None
    conf = checks.confidence(suspects[0]["bucket"] if suspects else None, top_loc["precision"] if top_loc else None,
                             searched, scope.deps.budget.repaired, len(issue.text().split()), pasted_count)
    env = {"summary": "", "items": locations, "issue_kind": kind, "suspect_files": suspects,
           "suspect_symbols": symbols, "edit_locations": locations, "search_terms": state.get("search_terms") or [],
           "missing_info": [{"item": m.item, "ask": m.ask} for m in missing], "needs_info": info_needed,
           "next_step": triage.next_step(kind, missing, top, state.get("listing") or []),
           "ref": state.get("ref"), "confidence": conf,
           "needs_human": checks.needs_human(conf, info_needed, kind), "dropped": scope.dropped.total}
    return {**env, "summary": render.summary(env)}


async def render_node(state: dict, scope: Scope) -> dict:
    env = envelope(state, scope)
    has_search = scope.catalog is not None and scope.catalog.tool("search") is not None
    lines = coverage.lines(scope.host.coverage if scope.host else None, bool(state.get("pasted_only")),
                           has_search, scope.dropped.total, state.get("notes") or [])
    text = render.as_json(env) if scope.incoming.wants_json else \
        render.markdown(env, lines, state.get("issue_number"))
    return {"reply": text, "data": env, "envelope": env, "coverage_lines": lines, "route": ""}


async def followup_node(state: dict, scope: Scope) -> dict:
    follow = scope.followup
    env = state.get("envelope") or {}
    if follow.kind == "json":
        return {"reply": render.as_json(env), "data": env, "route": ""}
    if follow.kind == "why":
        return {"reply": fu.why(follow.arg, env.get("suspect_files") or []), "route": ""}
    if follow.kind == "tests":
        return {"reply": fu.tests(env.get("suspect_files") or [], state.get("listing") or []), "route": ""}
    if follow.kind == "show":
        sks = {p: [Symbol.from_dict(s) for s in syms] for p, syms in (state.get("skeletons") or {}).items()}
        files = {p: d["text"] for p, d in (state.get("files") or {}).items()}
        shown = fu.show(follow.arg, sks, files)
        return {"reply": shown or f"`{follow.arg}` is not in the files I read. Name a file path and I'll read it "
                                   "in a new request.", "route": ""}
    return _rerun(state, scope, follow)


def _rerun(state: dict, scope: Scope, follow: fu.FollowUp) -> dict:
    """"look in X" or "re-run with this trace": the stored issue, with the hint or the new text, from discover."""
    issue = issue_doc(state["issue"])
    text = f"{issue.title}\n{issue.body}"
    hints = list(state.get("hints") or [])
    if follow.kind == "look":
        hints = [follow.arg, *hints]
    elif follow.arg:
        text = f"{text}\n\n{follow.arg}"
    pasted = tuple(PastedFile(p, d["text"]) for p, d in (state.get("files") or {}).items()) \
        if state.get("pasted_only") else ()
    scope.request = Request(text, repo_of(state.get("repo")), None, pasted, tuple(hints))
    if scope.request.repo is None and not pasted:
        return {"reply": prompts.NO_PRIOR, "route": ""}
    return {"route": "discover", "hints": hints, "notes": [], "issue_number": state.get("issue_number")}
