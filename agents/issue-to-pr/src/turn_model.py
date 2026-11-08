"""Graph nodes, localization (copied from the issue-localizer: Agentless's file → element → line hierarchy):
pick_files (LLM 1), skeletons (reads + code), pick_symbols (LLM 2), locate (LLM 3, at most 2 calls) and
assess (code: the localization record, Q3 when confidence is low). Model answers are checked in checks.py;
line numbers, scores and buckets come from code."""

from __future__ import annotations

import logging

import checks
import gather
import hitl
import localization
import locate as loc
import prompts
import scoring
import skeleton
import smalltalk
import steps
from candidates import Candidate
from llm import LlmError
from skeleton_types import Symbol
from turn_read import Scope, issue_doc, pause, repo_of

log = logging.getLogger("issue_to_pr.turn")

MAX_SKELETON_FILES = 6
Q3_OPTIONS = 4


def _cands(state: dict) -> list[Candidate]:
    return [Candidate.from_dict(c) for c in state.get("candidates") or []]


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
        have = await gather.read_files(scope.host, repo_of(state.get("repo")), state.get("ref"), paths, have)
    built = {p: skeleton.build(p, have[p].text) for p in paths if p in have}
    return {"route": "pick_symbols", "files": {p: d.to_dict() for p, d in have.items()},
            "skeletons": {p: [s.to_dict() for s in sk.symbols] for p, sk in built.items()}}


async def pick_symbols_node(state: dict, scope: Scope) -> dict:
    sks = {p: [Symbol.from_dict(s) for s in syms] for p, syms in (state.get("skeletons") or {}).items()}
    if not sks:
        return {"route": "assess", "symbols": []}
    shown = {p: skeleton.Skeleton(tuple(syms), True).for_model() for p, syms in sks.items()}
    try:
        data = await steps.pick_symbols(scope.deps, issue_doc(state["issue"]).text(), shown)
    except steps.OffTopic:
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    except LlmError as e:
        log.warning("pick_symbols failed: %s", e)
        return {"route": "assess", "symbols": []}
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
    locations = []
    for path, name, _ in chosen:
        text = files[path]["text"]
        quote, _why = answers.get((path, name), ("", ""))
        found = loc.find(path, text, sks[path][name], quote, len(text.split("\n")))
        if quote and found.precision == "symbol":
            scope.dropped.quotes += 1
        locations.append({"path": path, "symbol": name, "start_line": found.start_line, "end_line": found.end_line,
                          "precision": found.precision})
    return {"route": "assess", "locations": locations}


async def assess_node(state: dict, scope: Scope) -> dict:
    issue = issue_doc(state["issue"])
    searched = scope.host is not None and scope.host.coverage.searches > 0
    record = localization.build(state, searched, scope.deps.budget.repaired, len(issue.text().split()))
    if not record["suspect_files"]:
        return {"reply": prompts.NO_CANDIDATES.format(repo=_repo_name(state), why=""), "route": ""}
    if record["confidence"] != "low" or state.get("pasted_only"):  # pasted files: the user already chose
        return {"route": "fetch", "localization": record}
    options = (*record["suspect_files"][:Q3_OPTIONS], hitl.STOP)
    pending = hitl.Pending("pick_file", options=options, wants_json=scope.incoming.wants_json,
                           header="Which file should I change?", text=prompts.PICK_FILE)
    return pause(pending, localization=record)


def _repo_name(state: dict) -> str:
    repo = repo_of(state.get("repo"))
    return repo.full if repo else "the files you pasted"
