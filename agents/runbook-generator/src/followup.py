"""Follow-ups on the stored runbook: re-render views and fill-ins in code (0 calls), `add a step for X` (one
call, the same lint), and the one classify call for a short message that is none of these."""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

import lint
import llm
import placeholders
import prompts
import render
from draft import fact_dicts
from model import Runbook

log = logging.getLogger("runbook-generator.followup")

FOLLOWUP_MAX_WORDS = 40
VIEW_MAX_WORDS = 10
OFF_TOPIC, RUNBOOK_REQUEST, ABOUT_RUNBOOK = "off_topic", "runbook_request", "about_runbook"

_VIEWS = (
    (render.ROLLBACK, re.compile(r"\brollbacks?\b(?: steps)?(?: only)?|\bonly (?:the )?rollbacks?\b", re.IGNORECASE)),
    (render.CHECKS, re.compile(r"\b(?:checks?|read-?only|triage)(?: steps)? only\b|\bonly (?:the )?(?:checks?|"
                               r"read-?only|triage)\b", re.IGNORECASE)),
    (render.RISKY, re.compile(r"\bhigh[- ]risk\b|\b(?:state-changing|risky|dangerous)(?: steps)?(?: only)?\b", re.IGNORECASE)),
    (render.SHORT, re.compile(r"\bshorter\b|\bshort version\b|\bbrief(?:er)?\b|\bcondensed?\b", re.IGNORECASE)),
    (render.SOURCES, re.compile(r"\b(?:show|with|cite) (?:the )?(?:sources?|quotes?|evidence)\b|\bsources?\b", re.IGNORECASE)),
    (render.FULL, re.compile(r"\b(?:full|whole|entire) runbook\b|\bshow (?:it|the runbook) again\b", re.IGNORECASE)),
)
_JSON = re.compile(r"^\W*(?:(?:show|give|return|send)(?: (?:it|me|the runbook))? )?(?:as json|json only|in json)\W*$",
                   re.IGNORECASE)
_FILL = re.compile(r"^\s*(?:fill in|fill|set|replace)?\s*(<[^<>\n]{1,40}>|[A-Za-z][\w .:?'-]{0,40}?)\s*"
                   r"(?:=|:=|\bwith\b|\bis\b|\bto\b)\s*(.+?)\s*$", re.IGNORECASE)
_FILL_LEAD = re.compile(r"^\s*(?:fill in|fill|set|replace)\b|^\s*<[^<>\n]{1,40}>\s*(?:=|:=)", re.IGNORECASE)
_ADD = re.compile(r"^\s*(?:please\s+)?add (?:a |one |another )?(?:new )?step (?:for|to|that|about)\s+(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str                 # view | json | fill | add
    view: str = ""
    name: str = ""
    value: str = ""
    text: str = ""


def parse(text: str) -> Command | None:
    """The follow-up command in a short message, or None."""
    plain = " ".join((text or "").split())
    if not plain or len(plain.split()) > FOLLOWUP_MAX_WORDS:
        return None
    if _JSON.match(plain):
        return Command("json")
    add = _ADD.match(plain)
    if add:
        return Command("add", text=add.group(1))
    fill = _FILL.match(plain) if _FILL_LEAD.match(plain) else None
    if fill:
        return Command("fill", name=fill.group(1).strip(), value=placeholders.clean_value(fill.group(2)))
    for view, pattern in _VIEWS:
        if len(plain.split()) <= VIEW_MAX_WORDS and pattern.search(plain):
            return Command("view", view=view)
    return None


def apply_fill(runbook: Runbook, name: str, value: str) -> tuple[Runbook, bool]:
    """Every `<name>` in the stored runbook replaced by the user's value (their text, not the model's)."""
    def f(text: str | None) -> str | None:
        return placeholders.fill(text, name, value) if text else text
    steps = tuple(s.with_(title=f(s.title), action=f(s.action), command=f(s.command), expect=f(s.expect),
                          if_not=f(s.if_not), rollback=f(s.rollback)) for s in runbook.steps)
    updated = runbook.with_(title=f(runbook.title), purpose=f(runbook.purpose), steps=steps,
                            filled={**runbook.filled, name.strip("<> "): value})
    return updated, render.render(updated) != render.render(runbook)


async def add_step(deps: llm.TurnDeps, runbook: Runbook, request: str, source: str,
                   policy: Sequence[str] = ()) -> tuple[str, Runbook | None]:
    """('ok' | 'off_topic' | 'dropped' | 'failed', the runbook with the new step or None)."""
    messages = prompts.add_messages(request, [s.title for s in runbook.steps], fact_dicts(runbook.facts), policy)
    try:
        data = await llm.complete_json(deps, messages, prompts.ADD_CONTRACT, _validate_add)
    except llm.LlmError as e:
        log.warning("add step failed: %s", e)
        return "failed", None
    if data["status"] == "off_topic":
        return "off_topic", None
    linted = lint.lint([data["step"]], runbook.facts, _with_filled(source, runbook))
    if not linted.steps:
        return "dropped", runbook.with_(dropped=runbook.dropped + linted.dropped)
    new_steps, rewritten = lint.finalize(lint.Linted((*runbook.steps, *linted.steps)))
    return "ok", runbook.with_(steps=new_steps, rewritten=runbook.rewritten + rewritten)


def _validate_add(data: object) -> dict | None:
    if not isinstance(data, dict) or data.get("status") not in ("ok", "off_topic"):
        return None
    if data["status"] == "ok" and not isinstance(data.get("step"), dict):
        return None
    return data


def _with_filled(source: str, runbook: Runbook) -> str:
    return "\n".join([source, *runbook.filled.values()])


async def classify(deps: llm.TurnDeps, text: str, policy: Sequence[str] = ()) -> str:
    def validate(data: object) -> dict | None:
        ok = isinstance(data, dict) and data.get("status") in (OFF_TOPIC, RUNBOOK_REQUEST, ABOUT_RUNBOOK)
        return data if ok else None
    try:
        data = await llm.complete_json(deps, prompts.classify_messages(text, policy), prompts.CLASSIFY_CONTRACT,
                                       validate)
    except llm.LlmError as e:
        log.warning("classify failed: %s", e)
        return "failed"
    return data["status"]
