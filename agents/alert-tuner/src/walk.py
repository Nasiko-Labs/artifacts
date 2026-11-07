"""The approve -> write walk over one or more pull-request plans, its state on the A2A task between pauses.

    per plan:  branch card -> [Approve] -> create branch -> commit card -> [Approve] -> commit file
               -> PR card -> [Approve] -> open PR -> next plan's branch card, or the summary

Each card is its own `input-required` pause and allows exactly one write. Skip stops everything after it
and the reply says what is left behind (nothing is ever cleaned up or deleted by the agent). Typed text is
an edit instruction for that card only. A later card whose branch, base, path or content differs from what
card 1 showed stops the walk. A gateway approval (`ask`) pauses `auth-required` and re-sends the identical call.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import patch as patching
import plan as plans
import sender
from deps import TurnDeps
from plan import STAGES, Plan

log = logging.getLogger("alert_tuner.walk")

METADATA_KEY = "alert_tuner_walk"
NOT_WAITING = "This task isn't waiting on anything. Send a new request."
AUTHORIZED = "authorized"
MAX_FAILURES = 2
_YES = frozenset({"approve", "approved", "yes", "y", "ok", "okay", "go", "go ahead", "do it", "authorized", "create",
                  "commit", "open", "open it", "confirm"})
_NO = frozenset({"skip", "no", "n", "cancel", "stop", "deny", "denied", "reject", "don't", "dont", "abort"})
_MINUTES = re.compile(r"\b(\d{1,3})\s*(m|min|mins|minutes|h|hr|hours?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Walk:
    plans: tuple[Plan, ...]
    index: int = 0
    stage: str = "branch"
    shown: Mapping[str, str] = field(default_factory=dict)
    pending: Mapping[str, Any] | None = None
    failures: int = 0
    results: tuple[str, ...] = ()
    done: bool = False

    @property
    def plan(self) -> Plan:
        return self.plans[self.index]

    def to_metadata(self) -> dict:
        return {"plans": [p.to_dict() for p in self.plans], "index": self.index, "stage": self.stage,
                "shown": dict(self.shown), "pending": dict(self.pending) if self.pending else None,
                "failures": self.failures, "results": list(self.results), "done": self.done}

    @staticmethod
    def from_metadata(data: Mapping[str, Any]) -> Walk:
        return Walk(plans=tuple(Plan.from_dict(p) for p in data.get("plans") or ()), index=int(data.get("index") or 0),
                    stage=str(data.get("stage") or "branch"), shown=dict(data.get("shown") or {}),
                    pending=data.get("pending") or None, failures=int(data.get("failures") or 0),
                    results=tuple(str(r) for r in data.get("results") or ()), done=bool(data.get("done")))


@dataclass(frozen=True)
class Outcome:
    kind: str                     # complete | input_required | auth_required
    text: str
    metadata: dict | None = None
    hitl: dict | None = None
    envelope: dict | None = None


def start(found: tuple[Plan, ...], preamble: str = "") -> Outcome:
    return _show(Walk(found), preamble)


def _show(w: Walk, note: str = "") -> Outcome:
    if w.stage == "branch":
        w = replace(w, shown=w.plan.shown())
    text, hitl = plans.card(w.plan, w.stage, note)
    return Outcome("input_required", text, {METADATA_KEY: w.to_metadata()}, hitl)


async def resume(deps: TurnDeps, metadata: Mapping[str, Any], answer: str) -> Outcome:
    stored = metadata.get(METADATA_KEY) if metadata else None
    if not isinstance(stored, Mapping) or not stored.get("plans"):
        return Outcome("complete", NOT_WAITING)
    w = Walk.from_metadata(stored)
    if w.done or w.index >= len(w.plans) or w.stage not in STAGES:
        return Outcome("complete", NOT_WAITING)
    drift = _drift(w)
    if drift:
        return _stop(w, f"Stopped: the {drift} differs from what the first card showed, so I won't write anything more.")
    if w.pending:
        if answer.strip().casefold() != AUTHORIZED:
            return _stop(w, "The write was denied on the platform's approval card; nothing more was written.")
        return await _after(deps, w, await sender.write(deps, w.plan, w.stage, w.pending.get("arguments")))
    decision = _decision(answer)
    if decision == "skip":
        return _stop(w, "Skipped.")
    if decision == "approve":
        log.info("%s approved for %s", w.stage, w.plan.alert)
        return await _after(deps, w, await sender.write(deps, w.plan, w.stage))
    return _edit(w, answer)


def _decision(answer: str) -> str:
    word = (answer or "").strip().casefold().rstrip(".!")
    if not word or word in _NO:
        return "skip"
    return "approve" if word in _YES else "edit"


def _drift(w: Walk) -> str:
    if w.stage == "branch" or not w.shown:
        return "" if w.shown or w.stage == "branch" else "plan"
    now = w.plan.shown()
    names = {"branch": "branch", "base": "base branch", "path": "file path", "content": "diff"}
    return next((names[k] for k in names if now.get(k) != w.shown.get(k)), "")


def _edit(w: Walk, answer: str) -> Outcome:
    if w.stage == "branch":
        edited = plans.edit_branch(w.plan, answer)
    elif w.stage == "commit":
        edited = _edit_duration(w.plan, answer)
    else:
        edited = plans.edit_pr(w.plan, answer)
    if isinstance(edited, str):
        return _show(w, f"{edited} The card is unchanged.")
    updated = replace(w, plans=(*w.plans[:w.index], edited, *w.plans[w.index + 1:]))
    if w.stage == "commit":  # the user changed the diff on purpose: it is the new approved content
        updated = replace(updated, shown=edited.shown())
    return _show(updated, "Edited. Here is the card again:")


def _edit_duration(p: Plan, answer: str) -> Plan | str:
    m = _MINUTES.search(answer or "")
    if not m:
        return 'Here I can only change the new duration (e.g. "use 30m").'
    minutes = int(m.group(1)) * (60 if m.group(2).casefold().startswith("h") else 1)
    edited = patching.with_minutes(p.patch, minutes)
    if isinstance(edited, str):
        return f"I can't use {minutes}m: {edited}."
    new_title = p.title.replace(f"to {p.patch.new_value}", f"to {edited.new_value}")
    return replace(p, patch=edited, title=new_title, message=f"{new_title} (alert-tuner)",
                   body=p.body.replace(f"to {p.patch.new_value} would", f"to {edited.new_value} would"))


async def _after(deps: TurnDeps, w: Walk, result: sender.WriteResult) -> Outcome:
    p = w.plan
    if result.status == "paused":
        paused = replace(w, pending={"arguments": dict(result.arguments or {})})
        return Outcome("auth_required", "The platform needs your approval before this write:\n\n"
                       + plans.card(p, w.stage)[0], {METADATA_KEY: paused.to_metadata()},
                       {"hitl_request_id": result.hitl_request_id})
    if result.status == "ok":
        return _advance(replace(w, pending=None, failures=0), result.link)
    if result.status == "error" and w.failures + 1 < MAX_FAILURES:
        return _show(replace(w, pending=None, failures=w.failures + 1),
                     f"That write failed ({result.reason}). The approval was not used: Approve to retry, or Skip.")
    if result.status == "denied":
        return _stop(w, "The write was denied, so nothing more was written.")
    if result.status == "blocked":
        return _stop(w, plans.copyable(p, "Writing is blocked for this agent."))
    return _stop(w, plans.copyable(p, f"Not written: {result.reason or 'unknown reason'}."))


def _advance(w: Walk, link: str) -> Outcome:
    p = w.plan
    if w.stage == "branch":
        return _show(replace(w, stage="commit"), f"Created branch `{p.branch}`.")
    if w.stage == "commit":
        return _show(replace(w, stage="pr"), f"Committed `{p.patch.path}` to `{p.branch}`.")
    opened = f"Opened the pull request for `{p.alert}`: {link or '(the tool returned no link)'}"
    done = replace(w, results=(*w.results, opened))
    if w.index + 1 < len(w.plans):
        return _show(replace(done, index=w.index + 1, stage="branch", shown={}), opened)
    return _finish(done, "")


def leftovers(w: Walk) -> str:
    p = w.plan
    if w.stage == "branch":
        return "Nothing was written for this one."
    cleanup = f"Nothing was cleaned up; to remove it, delete branch `{p.branch}` in `{p.repo_full}`."
    if w.stage == "commit":
        return f"Branch `{p.branch}` exists in `{p.repo_full}` with no commit and no pull request. {cleanup}"
    return (f"Branch `{p.branch}` has the commit; no pull request is open. {cleanup} Or open the pull request "
            f"from `{p.branch}` to `{p.base}` by hand.")


def _stop(w: Walk, reason: str) -> Outcome:
    rest = [f"Not started: `{p.alert}`." for p in w.plans[w.index + 1:]]
    return _finish(w, "\n\n".join([reason, leftovers(w), *rest]))


def _finish(w: Walk, text: str) -> Outcome:
    done = replace(w, done=True, pending=None)
    body = "\n\n".join(x for x in (*w.results, text) if x)
    return Outcome("complete", body, {METADATA_KEY: done.to_metadata()})
