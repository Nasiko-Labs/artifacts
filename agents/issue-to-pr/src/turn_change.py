"""Graph nodes, the change: fetch (tools: the localized files, their tests and test config; complete files
only), then change (the private run directory, a baseline test run, ONE engine run, the diff computed in
code, the guards, the test run on the kept change). The run directory is removed at the end of the turn."""

from __future__ import annotations

import logging

import coverage
import gather
import guards
import hitl
import localization
import paths as p
import prompts
import render
import tests_runner as tr
import triage
from constraints import Constraints
from diffing import FileChange, changes
from turn_read import Scope, end, issue_doc, repo_of

log = logging.getLogger("issue_to_pr.change")

MAX_CODE_FILES = 10
MAX_SUSPECT_FILES = 6
MAX_CONFIG_FILES = 3
MAX_TOTAL_CHARS = 80_000
ENGINE_RESERVE_SECS = 40.0


def files_to_fetch(record: dict, listing: list[str]) -> tuple[list[str], list[str]]:
    """(code and test paths, config paths) in fetch order."""
    suspects = list(record.get("suspect_files") or [])[:MAX_SUSPECT_FILES]
    code = list(suspects)
    for path in suspects:
        test = triage.test_file_for(path, listing)
        if test and test not in code:
            code.append(test)
    config = [c for c in p.CONFIG_FILES if c in listing][:MAX_CONFIG_FILES]
    return code[:MAX_CODE_FILES], config


def pick_answer(answer: str, record: dict, listing: list[str]) -> str | None:
    """The file the Q3 answer names: an exact listed path, else the one listed path that ends with it."""
    wanted = answer.strip().strip("`").lstrip("./")
    if wanted in listing:
        return wanted
    tails = [x for x in listing if x.endswith(("/" + wanted, wanted))]
    return tails[0] if len(tails) == 1 else None


async def fetch_node(state: dict, scope: Scope) -> dict:
    record = dict(state.get("localization") or {})
    listing = list(state.get("listing") or [])
    if scope.pending is not None and scope.pending.kind == "pick_file":
        answer = hitl.answer_text(scope.incoming.text)
        if hitl.is_stop(answer):
            return end(prompts.CANCELLED)
        path = pick_answer(answer, record, listing)
        if path is None:
            return end(f"`{answer[:80]}` is not a file in the repository listing, so I stopped. Nothing was pushed.")
        record = localization.forced(record, path)
    code, config = files_to_fetch(record, listing)
    have = {k: gather.FileDoc.from_dict(v) for k, v in (state.get("files") or {}).items()}
    if not state.get("pasted_only") and scope.host is not None:
        repo, ref = repo_of(state.get("repo")), state.get("ref")
        have = {**have, **await gather.read_files(scope.host, repo, ref, code, have)}
        have = {**have, **await gather.read_files(scope.host, repo, ref, config, have)}
    originals, shas, excluded = _complete(code + config, have, state.get("pasted_only"))
    if not any(path in originals for path in code):
        why = f" ({'; '.join(f'{x}: {w}' for x, w in excluded)})" if excluded else ""
        return end(prompts.NO_FILES.format(why=why))
    has_search = scope.catalog is not None and scope.catalog.tool("search") is not None
    cov = coverage.lines(scope.host.coverage if scope.host else None, bool(state.get("pasted_only")), has_search,
                         scope.dropped.total, state.get("notes") or [])
    return {"route": "change", "localization": record, "originals": originals, "blob_shas": shas, "coverage": cov,
            "excluded": [list(x) for x in excluded],
            # the whole texts now live in `originals`; keep only the model-visible part (checkpoint size)
            "files": {k: {**v.to_dict(), "full": ""} for k, v in have.items()}}


def _complete(order: list[str], have: dict, pasted: bool) -> tuple[dict, dict, list]:
    originals, shas, excluded, total = {}, {}, [], 0
    paths = order if not pasted else list(dict.fromkeys([*order, *have]))
    for path in paths:
        doc = have.get(path)
        if doc is None:
            continue
        text = doc.full or doc.text
        if not doc.complete:
            excluded.append((path, "only part of the file could be read"))
            continue
        if total + len(text) > MAX_TOTAL_CHARS:
            excluded.append((path, "over the 80,000-character fetch cap"))
            continue
        originals[path], shas[path] = text, doc.sha
        total += len(text)
    return originals, shas, excluded


async def change_node(state: dict, scope: Scope) -> dict:
    deps = scope.deps
    engine, ws = deps.engine, deps.workspace
    if engine is None or ws is None or not ws.available():
        return end(prompts.NO_WORKSPACE_REPLY)
    if engine.busy:
        return end(prompts.BUSY_REPLY)
    root = ws.prepare(deps.context_id or "default")
    try:
        async with engine.lock:
            return await _run(state, scope, root)
    finally:
        ws.remove(root)


async def _run(state: dict, scope: Scope, root) -> dict:
    deps, ws = scope.deps, scope.deps.workspace
    originals: dict = state.get("originals") or {}
    rules = Constraints.from_dict(state.get("constraints"))
    issue = issue_doc(state["issue"])
    record = state.get("localization") or {}
    tests, why_not = tr.runnable(list(originals))
    baseline = None
    if tests:
        _, baseline = await deps.runner.execute(root, ws.materialize(root, "baseline", originals), tests)
    start = {**originals, **{c["path"]: c["final"] for c in state.get("changes") or []}} if scope.revision \
        else originals
    work = ws.materialize(root, "repo", start, {"AGENTS.md": prompts.AGENTS_MD})
    task = prompts.engine_task(issue.text(), list(originals), localization.hints(record), rules.words(), scope.revision)
    run = await deps.engine.run(session_id=state.get("session_id") if scope.revision else None, directory=work,
                                prompt=task, traceparent=deps.traceparent,
                                budget=max(10.0, deps.remaining() - ENGINE_RESERVE_SECS))
    revisions = int(state.get("revisions") or 0) + (1 if scope.revision else 0)
    failure = {"unavailable": prompts.ENGINE_UNAVAILABLE, "failed": prompts.ENGINE_FAILED_REPLY,
               "out_of_memory": prompts.ENGINE_OOM_REPLY}.get(run.error or "")
    if failure:
        return end(failure, session_id=run.session_id)
    collected = ws.collect(work)
    items = changes(originals, collected.files)
    outcome = guards.check(items, originals, state.get("listing") or [], rules,
                           guards.issue_says_test_wrong(issue.text()), collected.symlinks)
    discarded = [*outcome.discarded, *[(x, "binary or over 200 KB") for x in collected.skipped]]
    common = {"session_id": run.session_id, "revisions": revisions, "done_line": run.done_line,
              "timed_out": run.timed_out, "discarded": [list(d) for d in discarded], "warnings": list(outcome.warnings)}
    if outcome.refusal:
        return _terminal(state, prompts.REFUSED_REPLY.format(why=outcome.refusal), "refused",
                         {**common, "changes": [], "plan": None}, scope)
    if not outcome.kept:
        done = f" (its note: \"{run.done_line}\")" if run.done_line else ""
        return _terminal(state, prompts.NO_CHANGE_REPLY.format(done=done), "no_change",
                         {**common, "changes": [], "plan": None}, scope)
    kept_tests = await _after(deps, ws, root, originals, outcome.kept, baseline, why_not)
    update = {**common, "changes": [c.to_dict() for c in outcome.kept], "tests": kept_tests.to_dict(), "plan": None}
    if outcome.too_large:
        reply = prompts.TOO_LARGE_REPLY.format(files=len(outcome.kept), lines=outcome.changed_lines)
        return _terminal({**state, **update}, reply + "\n\n" + render.diff_block(list(outcome.kept)), "refused",
                         update, scope)
    return {"route": "propose", **update}


async def _after(deps, ws, root, originals: dict, kept: tuple[FileChange, ...], baseline, why_not: str) -> tr.Tests:
    after_files = {**originals, **{c.path: c.final for c in kept}}
    tests, why = tr.runnable(list(after_files))
    if not tests:
        return tr.Tests(False, "not_run", reason=why or why_not)
    result, _ = await deps.runner.execute(root, ws.materialize(root, "after", after_files), tests)
    return tr.with_baseline(result, baseline)


def _terminal(state: dict, reply: str, label: str, update: dict, scope: Scope) -> dict:
    merged = {**state, **update}
    env = render.envelope(merged, label)
    text = render.as_json(env) if scope.incoming.wants_json else reply
    return {**update, "reply": text, "data": env, "envelope": env, "route": ""}
