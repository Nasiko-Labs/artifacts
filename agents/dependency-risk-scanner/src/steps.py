"""The conversational graph nodes: intake (a new advisory and what to scan), the scan with its summary and
rendering, and the follow-up on a report (filter, open a PR). Every prompt that reads the user's words
carries the scope rule and the off-topic sentinel. The advisory is read in code first; the model fills
only gaps, and only with text found verbatim in the paste."""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import advisory as adv
import bump
import intake
import manifests
import pr_prep
import prompts
import render
import scan as scanning
import smalltalk
from deps import TurnDeps
from reader import ask_once
from scan import PASTED, Scan

log = logging.getLogger("dependency_risk.steps")

_MAX_FOLLOWUP_CHARS = 9_000
MAX_PR_REPOS = 3


def _reply(text: str, **extra) -> dict:
    return {"reply": text, "route": "", **extra}


async def intake_node(deps: TurnDeps, state: dict) -> dict:
    query = state["query"]
    if not deps.traceparent:
        return _reply(prompts.NO_TRACE_CONTEXT)
    pending = state.get("pending") or {}
    remembered = _remembered_repos(state)
    env = intake.envelope(query)
    if env is not None:
        advisory, repos = env
        if advisory is None:
            return _reply(prompts.NO_ADVISORY)
        return _ready(advisory, intake.Request(repos=repos or remembered))
    if pending.get("stage") == "range":
        parsed = adv.answer_range(scanning.advisory_of(pending["advisory"]), query)
        a = parsed.advisory if not parsed.advisory.missing() else _waived(parsed.advisory)
        return _ready(a, _pending_request(pending), remembered)
    if pending.get("stage") == "repos":
        request = intake.request_in(query)
        if request.repos or request.owner or request.pasted:
            return _ready(scanning.advisory_of(pending["advisory"]), request)
    return await _new_advisory(deps, query, remembered)


async def _new_advisory(deps: TurnDeps, query: str, remembered: tuple[str, ...]) -> dict:
    text = intake.advisory_text(query)[:intake.MAX_PASTE_CHARS]
    parsed = adv.parse_text(text)
    request = intake.request_in(query)
    if parsed.missing or not parsed.signal:
        answer = _object(await ask_once(deps, ({"role": "system", "content": prompts.EXTRACT_SYSTEM},
                                               {"role": "user", "content": prompts.fence_paste(text)})))
        if answer.get("status") == prompts.OFF_TOPIC and not parsed.signal and not request.pasted:
            return _reply(smalltalk.OFF_TOPIC_REPLY, pending={})
        parsed = adv.fill_from_model(parsed, answer, text)
    a = parsed.advisory
    if not a.package:
        return _reply(prompts.NO_ADVISORY, pending={})
    if a.missing():
        missing = " and ".join({"ecosystem": "ecosystem", "affected": "affected range"}[m] for m in a.missing())
        pending = {"stage": "range", "advisory": a.to_dict(), "repos": list(request.repos), "owner": request.owner,
                   "pasted": [list(p) for p in request.pasted], "cut": list(request.cut)}
        return _reply(prompts.ASK_RANGE.format(missing=missing), pending=pending, ask=True,
                      ask_header=prompts.ASK_RANGE_HEADER)
    return _ready(a, request, remembered)


def _waived(a: adv.Advisory) -> adv.Advisory:
    return replace(a, range_waived=True)


def _pending_request(pending: dict) -> intake.Request:
    return intake.Request(tuple(pending.get("repos") or ()), str(pending.get("owner") or ""),
                          tuple(tuple(p) for p in pending.get("pasted") or ()), tuple(pending.get("cut") or ()))


def _remembered_repos(state: dict) -> tuple[str, ...]:
    data = (state.get("report") or {}).get("scan") or {}
    return () if data.get("pasted") else tuple(data.get("repos") or ())


def _ready(a: adv.Advisory, request: intake.Request, remembered: tuple[str, ...] = ()) -> dict:
    if a.missing():
        return _reply(prompts.NO_ADVISORY, pending={})
    repos = request.repos or (() if request.owner or request.pasted else remembered)
    if not repos and not request.owner and not request.pasted:
        return _reply(prompts.ASK_REPOS, pending={"stage": "repos", "advisory": a.to_dict()}, ask=True)
    job = {"advisory": a.to_dict(), "repos": list(repos), "owner": request.owner,
           "pasted": [list(p) for p in request.pasted], "cut": list(request.cut)}
    return {"route": "scan", "job": job, "pending": {}}


def _object(text: str) -> dict:
    body = (text or "").strip().strip("`").removeprefix("json").strip()
    try:
        data = json.loads(body)
    except ValueError:
        start, end = body.find("{"), body.rfind("}")
        try:
            data = json.loads(body[start:end + 1]) if start != -1 and end > start else {}
        except ValueError:
            data = {}
    return data if isinstance(data, dict) else {}


async def scan_node(deps: TurnDeps, state: dict) -> dict:
    job = state.get("job") or {}
    a = scanning.advisory_of(job.get("advisory") or {})
    pasted = tuple((str(p), str(t)) for p, t in job.get("pasted") or ())
    if pasted:
        result: Scan | str = scanning.from_paste(a, pasted)
    else:
        result = await scanning.scan(deps, a, tuple(job.get("repos") or ()), str(job.get("owner") or ""),
                                     tuple(job.get("cut") or ()))
    if isinstance(result, str):
        return _reply(result)
    summary = await _summary(deps, result)
    text = render.report(result, summary)
    report = {"scan": result.to_dict(), "summary": summary, "show_all": False, "text": text,
              "pasted_files": [list(p) for p in pasted]}
    return _reply(text, report=report, envelope=render.envelope(result, summary))


async def _summary(deps: TurnDeps, result: Scan) -> str:
    if not result.rows:
        return render.code_summary(result)
    facts = render.facts(result)
    answer = _object(await ask_once(deps, ({"role": "system", "content": prompts.SUMMARY_SYSTEM},
                                           {"role": "user", "content": prompts.fence_facts(facts)})))
    summary = " ".join(str(answer.get("summary") or "").split())
    if answer.get("status") == "ok" and render.checked_summary(summary, facts):
        return summary
    log.info("model summary dropped; using the code-built one")
    return render.code_summary(result)


async def followup_node(deps: TurnDeps, state: dict) -> dict:
    report = state.get("report") or {}
    result = Scan.from_dict(report.get("scan") or {})
    command = intake.command(state["query"], result.repos)
    if not command.kind:
        if not deps.traceparent:
            return _reply(prompts.NO_TRACE_CONTEXT)
        command = await _classified(deps, state["query"], report, result)
        if command is None:
            return _reply(smalltalk.OFF_TOPIC_REPLY)
        if isinstance(command, str):
            return _reply(command)
    if command.kind == "filter":
        summary = str(report.get("summary") or "")
        text = render.report(result, summary, command.show_all)
        return _reply(text, report={**report, "show_all": command.show_all, "text": text},
                      envelope=render.envelope(result, summary, command.show_all))
    return _pr_request(deps, report, result, command)


async def _classified(deps: TurnDeps, query: str, report: dict, result: Scan):
    """The model's reading of a follow-up code didn't recognise: a Command, a short answer, or None."""
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_facts(str(report.get('text') or '')[:_MAX_FOLLOWUP_CHARS])}"
    text = await ask_once(deps, ({"role": "system", "content": system},
                                 {"role": "user", "content": prompts.strip_markers(query)}))
    word = text.strip().strip("`.").strip()
    if not word or word.startswith(prompts.OFF_TOPIC):
        return None
    if word.startswith(prompts.PR):
        named = intake.repos_in(word[len(prompts.PR):])
        return intake.Command("pr", repos=tuple(r for r in named if r in result.repos))
    if word in (prompts.FILTER_EXPOSED, prompts.FILTER_ALL):
        return intake.Command("filter", show_all=word == prompts.FILTER_ALL)
    return text


def _pr_request(deps: TurnDeps, report: dict, result: Scan, command: intake.Command) -> dict:
    a = result.advisory
    if a.kind == "deprecation":
        use = f"Replace `{a.package}` with `{a.replacement}` where it is declared." if a.replacement else \
            f"Plan a replacement for `{a.package}` where it is declared."
        return _reply(prompts.NO_PR_FOR_DEPRECATION.format(suggestion=use))
    candidates = render.repos_with_upgrade(result.rows)
    if not candidates:
        return _reply(prompts.NOTHING_TO_UPGRADE)
    if result.pasted:
        return _reply(_pasted_diffs(report, result))
    known = {c.casefold(): c for c in candidates}
    wrong = [r for r in command.repos if r.casefold() not in known]
    if wrong:
        return _reply(f"{', '.join(wrong)} has nothing to upgrade in the last report. Repositories with an upgrade: "
                      f"{', '.join(candidates)}.")
    chosen = [known[r.casefold()] for r in command.repos] or (candidates if command.every or len(candidates) == 1
                                                              else [])
    if not chosen:
        return _reply(f"Which repository? Say \"open the PR for <repo>\" with one of: {', '.join(candidates)}.")
    over = chosen[MAX_PR_REPOS:]
    note = f"Opening PRs for at most {MAX_PR_REPOS} repositories in one go; not done: {', '.join(over)}." if over else ""
    return {"route": "draft", "pr_repos": chosen[:MAX_PR_REPOS], "reply": note}


def _pasted_diffs(report: dict, result: Scan) -> str:
    texts = {str(p): str(t) for p, t in report.get("pasted_files") or ()}
    out = [("These manifests were pasted, so there is no repository to open a pull request in. The change to make "
            "by hand:")]
    for row in pr_prep.fixable(result, PASTED):
        found = manifests.find(row.manifest, texts.get(row.manifest, ""), row.package, row.ecosystem)
        dep = next((d for d in found if d.line == row.line), None)
        edit = bump.edit(texts.get(row.manifest, ""), dep, row.bump_to or "") if dep else None
        out.append(f"```diff\n{edit.diff}\n```" if edit else
                   f"- `{row.manifest}`: change `{row.package}` from `{row.declared}` to {row.bump_to} by hand.")
    out.append("The lockfile is not updated: run the package manager to regenerate it.")
    return "\n\n".join(out)


async def draft_node(deps: TurnDeps, state: dict) -> dict:
    report = state.get("report") or {}
    repos = list(state.get("pr_repos") or ())
    if not report or not repos:
        return _reply(prompts.NO_REPORT)
    if not deps.traceparent:
        return _reply(prompts.NO_TRACE_CONTEXT)
    result = Scan.from_dict(report.get("scan") or {})
    plan = await pr_prep.prepare(deps, result, repos[0])
    if isinstance(plan, str):
        return _reply("\n\n".join(x for x in (state.get("reply") or "", plan) if x))
    keep = {"advisory": result.advisory.to_dict(), "repos": list(result.repos), "bases": dict(result.bases),
            "rows": [r.to_dict() for r in result.rows if r.repo in repos[1:]]}
    return {"proposal": {"plan": plan.to_dict(), "queue": repos[1:], "scan": keep}, "route": ""}
