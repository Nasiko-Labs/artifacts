"""The conversational graph nodes: intake (a new scan request), the scan itself with its headline and
rendering, and the follow-up on a report (evidence, draft, rescan). Every prompt that reads the user's
words carries the scope rule and the off-topic sentinel."""

from __future__ import annotations

import json
import logging

import draft
import intake
import pasted
import prompts
import render
import smalltalk
from deps import TurnDeps
from history import Flaky, analyse
from reader import ask_once
from scan import Params, Scan, scan

log = logging.getLogger("flaky_hunter.turns")

_MAX_FOLLOWUP_CHARS = 9_000


async def intake_node(deps: TurnDeps, state: dict) -> dict:
    query = state["query"]
    remembered = Params.from_dict(state.get("params")) if state.get("params") else None
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    if pasted.has_runs(query):
        return {"route": "scan", "pasted": True, "scan_params": (remembered or Params()).to_dict()}
    envelope = intake.envelope_params(query)
    if envelope is not None:
        return _go(envelope)
    pending = Params.from_dict(state.get("pending")) if state.get("pending") else None
    params = intake.params_in(query, pending or remembered)
    answer = _object(await ask_once(deps, ({"role": "system", "content": prompts.INTAKE_SYSTEM},
                                           {"role": "user", "content": prompts.strip_markers(query)})))
    status = answer.get("status")
    if status == "off_topic" and not (pending and intake.repo_in(query)):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if not params.repo:
        model_repo = intake.repo_in(str(answer.get("repo") or ""))
        params = Params(model_repo, params.workflow, params.branch, params.runs) if model_repo in query else params
    if not params.repo:
        return {"reply": prompts.ASK_REPO, "pending": params.to_dict(), "ask": True, "route": ""}
    return _go(params)


def _go(params: Params) -> dict:
    return {"route": "scan", "scan_params": params.to_dict(), "pending": {}, "pasted": False}


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
    params = Params.from_dict(state.get("scan_params"))
    if state.get("pasted"):
        runs, attempts = pasted.parse(state["query"])
        result: Scan | str = Scan(params, pasted.PASTED, pasted.PASTED, "", "", tuple(runs), len(attempts),
                                  len(attempts), 0, 0, analyse(runs, attempts, None, deps.now), pasted=True)
    else:
        result = await scan(deps, params)
    if isinstance(result, str):
        return {"reply": result, "route": "", "params": params.to_dict()}
    headline = await _headline(deps, result)
    text = render.report(result, headline)
    envelope = render.envelope(result, headline or render.code_headline(result) or render.header(result))
    report = {"repo": params.repo if not result.pasted else "", "base": result.base, "base_sha": result.base_sha,
              "flaky": [f.to_dict() for f in result.ranking.flaky], "text": text, "envelope": envelope}
    out = {"reply": text, "report": report, "envelope": envelope, "route": ""}
    return {**out, "params": params.to_dict()} if not result.pasted else out


async def _headline(deps: TurnDeps, result: Scan) -> str:
    if not result.ranking.flaky and not result.ranking.broken:
        return ""
    facts = render.facts(result)
    answer = _object(await ask_once(deps, ({"role": "system", "content": prompts.HEADLINE_SYSTEM},
                                           {"role": "user", "content": prompts.fence_facts(facts)})))
    headline = str(answer.get("headline") or "").strip()
    if answer.get("status") == "ok" and render.checked_headline(headline, facts):
        return headline
    log.info("model headline dropped; using the code-built one")
    return render.code_headline(result)


async def followup_node(deps: TurnDeps, state: dict) -> dict:
    report = state.get("report") or {}
    params = Params.from_dict(state.get("params"))
    flaky = tuple(Flaky.from_dict(f) for f in report.get("flaky") or ())
    command = intake.command(state["query"], params)
    if not command.kind:
        if not deps.traceparent:
            return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
        command = await _classified(deps, state["query"], report, params)
        if command is None:
            return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
        if isinstance(command, str):
            return {"reply": command, "route": ""}
    if command.kind == "rescan":
        if not params.repo:
            return {"reply": "Those results came from pasted logs; paste the logs again to recompute.", "route": ""}
        return _go(command.params or params)
    rank = command.rank or (1 if command.kind == "draft" else 0)
    item = next((f for f in flaky if f.rank == rank), None)
    if item is None:
        listed = f"#1 to #{len(flaky)}" if flaky else "none"
        return {"reply": f"There is no #{rank} in the last report (listed: {listed}).", "route": ""}
    if command.kind == "evidence":
        return {"reply": render.evidence(item), "route": ""}
    return {"route": "draft", "rank": rank, "skip_ticket": command.skip_ticket}


async def _classified(deps: TurnDeps, query: str, report: dict, params: Params):
    """The model's reading of a follow-up code didn't recognise: a Command, a short answer, or None (off topic)."""
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_facts(str(report.get('text') or '')[:_MAX_FOLLOWUP_CHARS])}"
    text = await ask_once(deps, ({"role": "system", "content": system},
                                 {"role": "user", "content": prompts.strip_markers(query)}))
    word = text.strip().strip("`.").strip()
    if word.startswith(prompts.OFF_TOPIC) or not word:
        return None
    for prefix, kind in ((prompts.EVIDENCE, "evidence"), (prompts.DRAFT, "draft")):
        if word.startswith(prefix):
            rank = word[len(prefix):].strip().lstrip("#")
            return intake.Command(kind, int(rank) if rank.isdigit() else 0)
    if word == prompts.RESCAN:
        return intake.Command("rescan", params=params)
    return text


async def draft_node(deps: TurnDeps, state: dict) -> dict:
    report = state.get("report") or {}
    item = next((Flaky.from_dict(f) for f in report.get("flaky") or () if int(f.get("rank") or 0) == state.get("rank")),
                None)
    if item is None:
        return {"reply": prompts.NO_REPORT, "route": ""}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    plan = await draft.prepare(deps, report, item)
    if isinstance(plan, str):
        return {"reply": plan, "route": ""}
    return {"proposal": {"plan": plan.to_dict(), "skip_ticket": bool(state.get("skip_ticket"))}, "route": ""}
