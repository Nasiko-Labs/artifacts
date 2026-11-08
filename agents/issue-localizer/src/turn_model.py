"""Graph nodes, model side: pick_files (LLM 1), skeletons (reads + code), pick_symbols (LLM 2), locate (LLM 3),
triage (code + at most one LLM call) and render (code). Model answers are checked in checks.py; line numbers,
scores and buckets come from code."""

from __future__ import annotations

import logging

import checks
import gather
import locate as loc
import prompts
import scoring
import skeleton
import smalltalk
import steps
import triage
from candidates import Candidate
from llm import LlmError
from skeleton_types import Symbol
from turn_read import Scope, issue_doc, repo_of

log = logging.getLogger("issue_localizer.turn")

MAX_SKELETON_FILES = 6


def _cands(state: dict) -> list[Candidate]:
    return [Candidate.from_dict(c) for c in state.get("candidates") or []]


def _known(state: dict) -> checks.Known:
    names = [s["name"] for syms in (state.get("skeletons") or {}).values() for s in syms]
    return checks.Known(state.get("listing") or [], names, issue_doc(state["issue"]).text())


async def pick_files_node(state: dict, scope: Scope) -> dict:
    issue = issue_doc(state["issue"])
    cands = _cands(state)
    if not cands:
        return {"route": "skeletons", "picks": []}
    try:
        data = await steps.pick_files(scope.deps, issue.text(), cands)
    except steps.OffTopic:
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    except LlmError as e:
        log.warning("pick_files failed: %s", e)
        return {"route": "skeletons", "picks": [], "notes": [*state.get("notes", []), prompts.MODEL_FAILED]}
    picks = checks.file_picks(data, {c.path for c in cands}, scope.dropped)
    return {"route": "skeletons", "picks": [list(p) for p in picks]}


async def skeletons_node(state: dict, scope: Scope) -> dict:
    ranks = {p: i + 1 for i, (p, _) in enumerate(state.get("picks") or [])}
    ranked = scoring.suspects(_cands(state), ranks)
    paths = [s.path for s in ranked[:MAX_SKELETON_FILES]]
    have = {p: gather.FileDoc.from_dict(d) for p, d in (state.get("files") or {}).items()}
    if scope.host is not None:
        repo = repo_of(state.get("repo"))
        have = await gather.read_files(scope.host, repo, state.get("ref"), paths, have)
    built = {p: skeleton.build(p, have[p].text) for p in paths if p in have}
    return {"route": "pick_symbols", "files": {p: d.to_dict() for p, d in have.items()},
            "skeletons": {p: [s.to_dict() for s in sk.symbols] for p, sk in built.items()}}


async def pick_symbols_node(state: dict, scope: Scope) -> dict:
    sks = {p: [Symbol.from_dict(s) for s in syms] for p, syms in (state.get("skeletons") or {}).items()}
    if not sks:
        return {"route": "triage", "symbols": []}
    shown = {p: skeleton.Skeleton(tuple(syms), True).for_model() for p, syms in sks.items()}
    try:
        data = await steps.pick_symbols(scope.deps, issue_doc(state["issue"]).text(), shown)
    except steps.OffTopic:
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    except LlmError as e:
        log.warning("pick_symbols failed: %s", e)
        return {"route": "triage", "symbols": []}
    names = {p: {s.name for s in syms} for p, syms in sks.items()}
    picks = checks.symbol_picks(data, names, scope.dropped)
    return {"route": "locate", "symbols": [list(p) for p in picks]}


async def locate_node(state: dict, scope: Scope) -> dict:
    chosen = state.get("symbols") or []
    files = state.get("files") or {}
    sks = {p: {s["name"]: Symbol.from_dict(s) for s in syms} for p, syms in (state.get("skeletons") or {}).items()}
    blocks = []
    for path, name, _ in chosen:
        sym, text = sks[path][name], files[path]["text"]
        lines = loc.code_lines(text, sym, len(text.split("\n")))[:steps.MAX_SYMBOL_LINES]
        blocks.append((path, name, "\n".join(lines)))
    answers: dict = {}
    if blocks:
        try:
            data = await steps.locate(scope.deps, issue_doc(state["issue"]).text(), blocks)
            answers = checks.location_picks(data, {(p, n) for p, n, _ in chosen}, scope.dropped)
        except steps.OffTopic:
            return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
        except LlmError as e:
            log.warning("locate failed: %s", e)
    known = _known(state)
    locations = []
    for path, name, _ in chosen:
        text = files[path]["text"]
        quote, why = answers.get((path, name), ("", ""))
        found = loc.find(path, text, sks[path][name], quote, len(text.split("\n")))
        if quote and found.precision == "symbol":
            scope.dropped.quotes += 1
        locations.append({"path": path, "symbol": name, "start_line": found.start_line, "end_line": found.end_line,
                          "precision": found.precision, "quote": found.quote,
                          "why": checks.reason(why, known, scope.dropped) if why else ""})
    return {"route": "triage", "locations": locations}


async def triage_node(state: dict, scope: Scope) -> dict:
    issue = issue_doc(state["issue"])
    cands = _cands(state)
    kind = triage.kind_override(issue.title, issue.text(), issue.labels, [c.path for c in cands])
    if kind is None:
        try:
            kind = triage.clean_kind(await steps.kind(scope.deps, issue.text()))
        except LlmError:
            kind = "unclear"
    return {"route": "render", "issue_kind": kind}
