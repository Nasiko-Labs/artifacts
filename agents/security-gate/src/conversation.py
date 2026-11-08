"""The conversational nodes: intake (what to scan, read in code first), the follow-up on a stored report,
and the write preparation. Actions this agent never takes (merge, approve ...) are declined in code with
zero calls. Every prompt that reads the user's words carries the scope rule."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict

import gate
import prompts
import render
import request
import smalltalk
import writes
from deps import TurnDeps
from explain import _SAFE_CLAIM, load_object
from reader import ask_once
from records import GateReport
from request import Asked

log = logging.getLogger("security_gate.conversation")

_MAX_FOLLOWUP_CHARS = 9_000
_POST = re.compile(r"^\s*(?:please\s+)?(?:post|comment|publish|send|draft)\b(?:\s+(?:it|this|that|the (?:summary|"
                   r"report|comment)))?(?:\s+(?:on|to)\s+(?:the\s+)?(?:pr|pull request))?\s*[.!]*\s*$", re.IGNORECASE)
_SKIP = re.compile(r"^\s*(?:skip|no thanks|don'?t post)\s*[.!]*\s*$", re.IGNORECASE)
_IGNORE = re.compile(r"^\s*(?:please\s+)?ignore\s+(?:the\s+)?(?:rule\s+)?[`'\"]?(.+?)[`'\"]?(?:\s+rule)?"
                     r"(?:\s+for this review)?\s*[.!]*\s*$", re.IGNORECASE)
_SHOW_EXISTING = re.compile(r"^\s*(?:show|list|include)(?: me)?(?: the)? existing\b", re.IGNORECASE)
_STRICT_ON = re.compile(r"^\s*(?:use |switch to |turn on |re-?run (?:in|with) )?strict(?: mode)?\s*[.!]*\s*$",
                        re.IGNORECASE)
_STRICT_OFF = re.compile(r"^\s*(?:not strict|turn off strict|no strict|normal mode)\s*[.!]*\s*$", re.IGNORECASE)
_RESCAN = re.compile(r"^\s*(?:re-?scan|scan (?:it )?again|re-?run(?: it)?|gate (?:it )?again)\b", re.IGNORECASE)


def report_reply(state: dict, report: GateReport, include_existing: bool = False) -> dict:
    envelope = render.envelope(report)
    reply = render.as_json(envelope) if state.get("json_mode") else render.card(report, include_existing)
    out = {"reply": reply, "report": report.to_dict(), "envelope": envelope, "route": ""}
    return {**out, "repo": report.pr.repo} if report.pr else out


async def intake(deps: TurnDeps, state: dict) -> dict:
    query, remembered = state["query"], state.get("repo") or ""
    if request.is_forbidden(query):
        return {"reply": prompts.DECLINE_ACTION, "route": ""}
    asked = request.parse(query, remembered)
    if asked.pasted:
        return {"asked": asdict(asked), "route": "pasted"}
    pending = state.get("pending") or {}
    if not asked.number and pending.get("number") and request.repo_in(query):
        asked = Asked(**{**pending, "repo": request.repo_in(query)})
    if asked.number and not asked.repo:
        return {"reply": prompts.ASK_REPO.format(number=asked.number), "pending": asdict(asked), "route": ""}
    if asked.has_pr:
        return {"asked": asdict(asked), "pending": {}, "route": "pr"}
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    answer = load_object(await ask_once(deps, ({"role": "system", "content": prompts.INTAKE_SYSTEM},
                                               {"role": "user", "content": query}))) or {}
    status = answer.get("status")
    if status == "off_topic":
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    repo, number = request.pr_ref(f"PR {answer.get('pr')} in {answer.get('repo')}", remembered)
    if status == "pr" and number and repo and repo in query and number in query:
        return {"asked": asdict(Asked(repo, number, asked.strict, asked.include_existing, asked.json_mode,
                                      asked.language)), "route": "pr"}
    return {"reply": prompts.NEED_INPUT, "route": ""}


def names_new_input(state: dict) -> bool:
    """A different PR, or something pasted: a new gate, not a follow-up."""
    query = state.get("query") or ""
    asked = request.parse(query, "")
    if asked.pasted:
        return True
    report = GateReport.from_dict(state.get("report"))
    if not asked.number:
        return False
    if report is None or report.pr is None:
        return True
    return not (asked.number == report.pr.number and (not asked.repo or asked.repo == report.pr.repo))


async def followup(deps: TurnDeps, state: dict) -> dict:
    report = GateReport.from_dict(state.get("report"))
    if report is None:
        return {"reply": prompts.NO_REPORT, "route": ""}
    query = state["query"]
    handled = _in_code(state, report, query)
    if handled is not None:
        return handled
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    context = (render.card(report, include_existing=True))[:_MAX_FOLLOWUP_CHARS]
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_report(context)}"
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": query}))
    word = text.strip().strip("`.").strip()
    if word.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if word == prompts.DRAFT:
        return {"route": "write"}
    if word == prompts.NEEDS_PULL:
        return _rescan(report)
    if not text.strip() or _SAFE_CLAIM.search(text):
        return {"reply": f"I can't say the code is secure: the gate only reports what these rules found.\n\n"
                         f"{render.verdict_line(report)}", "route": ""}
    return {"reply": f"{render.clean(text.strip())}\n\n{prompts.NOT_AN_AUDIT}", "route": ""}


def _in_code(state: dict, report: GateReport, query: str) -> dict | None:
    if request.is_forbidden(query):
        return {"reply": prompts.DECLINE_ACTION, "route": ""}
    if _POST.match(query):
        return {"route": "write"}
    if _SKIP.match(query):
        return {"reply": "OK, nothing was posted.", "route": ""}
    if _SHOW_EXISTING.match(query):
        return report_reply(state, report, include_existing=True)
    if _STRICT_ON.match(query) or _STRICT_OFF.match(query):
        return report_reply(state, gate.with_strict(report, bool(_STRICT_ON.match(query))))
    if _RESCAN.match(query):
        return _rescan(report)
    if request.wants_json(query) and len(query) < 40:
        return report_reply({**state, "json_mode": True}, report)
    m = _IGNORE.match(query)
    if m:
        rules = gate.matching_rules(report, m.group(1))
        if not rules:
            ids = ", ".join(f"`{x}`" for x in dict.fromkeys(f.rule_id for f in report.findings)) or "none"
            return {"reply": f"No rule in this report matches \"{render.clean(m.group(1))}\". Rules: {ids}.", "route": ""}
        return report_reply(state, gate.ignore(report, rules))
    return None


def _rescan(report: GateReport) -> dict:
    if report.pr is None:
        return {"reply": "That report was of pasted input; paste the new version to scan it.", "route": ""}
    asked = Asked(report.pr.repo, report.pr.number, report.strict)
    return {"asked": asdict(asked), "route": "pr"}


async def write(deps: TurnDeps, state: dict) -> dict:
    report = GateReport.from_dict(state.get("report"))
    if report is None:
        return {"reply": prompts.NO_REPORT, "route": ""}
    prepared = await writes.prepare_comment(deps, report, state.get("outcomes") or {})
    if prepared.proposal is None:
        return {"reply": prepared.reply, "route": ""}
    return {"reply": "", "proposal": prepared.proposal.to_dict(), "route": ""}
