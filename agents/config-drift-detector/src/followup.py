"""Follow-ups on the last report in this conversation, answered in code from the stored drift records:
`only <env|type|resource>`, `ignore tags`, `who changed <resource>` (with newly pasted audit events
re-matched), `expected vs actual <resource>`, `show extras`. The phrases are matched in code first;
anything else is classified once by the model (scope-checked), then dispatched the same way."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import audit
import drift as dr
import prompts
import render
import smalltalk
from deps import TurnDeps
from envelope import coverage_notes
from inputs import Inputs
from llm import ask_json
from turn import Outcome, from_state, present, to_state

ACTIONS = ("only", "ignore_tags", "who_changed", "details", "show_extras")
_IGNORE_TAGS = re.compile(r"\b(?:ignore|hide|without|skip|drop|exclude)\s+(?:the\s+|all\s+)?(?:tags?|labels?)\b",
                          re.IGNORECASE)
_ONLY = re.compile(r"\bonly\s+(?:show\s+)?(?:the\s+)?(.+?)\s*(?:ones|drifts?|resources?)?\s*[.?!]*$", re.IGNORECASE)
_WHO = re.compile(r"\bwho\s+(?:changed|modified|edited|touched|did)\s*(.*?)[?.!]*$", re.IGNORECASE)
_DETAILS = re.compile(r"\b(?:expected\s+vs\.?\s+actual|details?)\s*(?:for|of|on)?\s*(.*?)[?.!]*$", re.IGNORECASE)
_EXTRAS = re.compile(r"\b(?:show|include)\s+(?:the\s+)?extras?\b", re.IGNORECASE)
_VAGUE = re.compile(r"^(?:it|this|that|them|these|those|everything|all|anything|by hand|manually)?$", re.IGNORECASE)
_ARTICLE = re.compile(r"^(?:the|a|an)\s+", re.IGNORECASE)


@dataclass(frozen=True)
class Ask:
    action: str
    target: str = ""


def match(text: str) -> Ask | None:
    """The follow-up the user's words name, read in code."""
    line = " ".join((text or "").split())
    if _EXTRAS.search(line):
        return Ask("show_extras")
    if _IGNORE_TAGS.search(line):
        return Ask("ignore_tags")
    for action, pattern in (("who_changed", _WHO), ("details", _DETAILS), ("only", _ONLY)):
        m = pattern.search(line)
        if m:
            return Ask(action, _target(m.group(1)))
    return None


def _target(text: str) -> str:
    clean = _ARTICLE.sub("", (text or "").strip().strip("`'\"").strip())
    return "" if _VAGUE.match(clean) else clean


async def classify(deps: TurnDeps, text: str) -> Ask | str:
    """The model's reading of a message the phrases did not match: an Ask, `OFF_TOPIC`, or "drift"."""
    answer = await ask_json(deps, ({"role": "system", "content": prompts.INTAKE_SYSTEM},
                                   {"role": "user", "content": prompts.fence_user(text)})) or {}
    intent = str(answer.get("intent") or "")
    if intent == prompts.OFF_TOPIC or intent.casefold() == "off_topic":
        return prompts.OFF_TOPIC
    action = str(answer.get("action") or "")
    if intent == "followup" and action in ACTIONS:
        target = str(answer.get("target") or "")
        return Ask(action, _target(target) if target and target.casefold() in text.casefold() else "")
    return "drift"


def answer(ask: Ask, report: dict, inp: Inputs, deps: TurnDeps, wants_json: bool) -> Outcome:
    a = from_state(report)
    state = dict(report)
    if inp.audit_text:  # newly pasted audit events: re-match every drift
        events = audit.parse(inp.audit_text)
        a = replace(a, audit_given=bool(events),
                    drifts=tuple(replace(d, changed_by=audit.changed_by(events, d.ids, deps.now)) for d in a.drifts))
        state = to_state(a, repo=report.get("repo") or "", headline=report.get("headline") or "",
                         notes=coverage_notes(a, False), gaps=bool(report.get("gaps")))
    drifts = a.drifts
    lead = ""
    if ask.action == "ignore_tags":
        drifts = dr.without_tags(drifts)
        lead = f"Ignoring tag and label changes: {len(drifts)} of {len(a.drifts)} drifted attributes left."
    elif ask.action in ("only", "who_changed", "details") and ask.target:
        drifts = dr.matching(drifts, ask.target)
        if not drifts:
            return Outcome(prompts.NO_MATCH_TARGET.format(target=ask.target,
                                                          resources=", ".join(dr.resources(a.drifts)) or "none"))
        lead = f"Showing {len(drifts)} of {len(a.drifts)} drifted attributes for \"{ask.target}\"."
    if ask.action == "who_changed":
        lead = (lead + "\n\n" + _who_lines(drifts)).strip()
    if ask.action == "show_extras" and not a.extras:
        lead = "No attributes are set live but missing from the code."
    out = present(a, drifts, state, show_extras=ask.action == "show_extras", wants_json=wants_json, lead=lead,
                  detail=ask.action == "details")
    return replace(out, report=state)


def _who_lines(drifts) -> str:
    rows = []
    for name in dr.resources(drifts):
        first = next(d for d in drifts if d.resource == name)
        rows.append(f"- {render.code(name)}: {render.who(first.changed_by)}")
    return "**Who changed it by hand** (from pasted audit events only)\n" + "\n".join(rows)


def decline() -> Outcome:
    return Outcome(smalltalk.OFF_TOPIC_REPLY)
