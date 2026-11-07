"""The conversational nodes: intake (which pull request, read in code first), the follow-up on a review,
and the write preparation. Every prompt that reads the user's words carries the scope rule."""

from __future__ import annotations

import logging
import re

import prompts
import render
import smalltalk
import targets
import writes
from analysis import load_object
from deps import TurnDeps
from reader import ask_once
from report import Review
from targets import Target

log = logging.getLogger("pr_reviewer.intake")

_MAX_FOLLOWUP_CHARS = 9_000
_POST = re.compile(r"^\s*(?:please\s+)?(?:post|comment|publish|send)\b(?:\s+(?:it|this|that|the review|"
                   r"the summary))?(?:\s+(?:on|to)\s+(?:the\s+)?(?:pr|pull request))?\s*[.!]*\s*$", re.IGNORECASE)
_SHOW_CRITERIA = re.compile(r"^\s*(?:show|list)(?: me)? (?:the )?criteria\b", re.IGNORECASE)
_SHOW_SIMILAR = re.compile(r"^\s*(?:show|list)(?: me)? (?:the )?similar\b", re.IGNORECASE)
_SKIP = re.compile(r"^\s*(?:skip|no thanks|don'?t post)\s*[.!]*\s*$", re.IGNORECASE)
_TICKET_OVERRIDE = re.compile(r"\bticket\b|\binstead\b|\bagainst\b", re.IGNORECASE)


def names_new_target(state: dict) -> bool:
    """A pull request other than the one last reviewed ("post it on acme/shop#412" is a follow-up)."""
    target = targets.parse(state.get("query") or "", state.get("repo") or "")
    review = Review.from_dict(state.get("report"))
    if target is None or review is None:
        return target is not None
    return not (target.repo_full == review.repo_full and target.number == review.number)


def _go(target: Target) -> dict:
    return {"target": target.to_dict(), "repo": target.repo_full, "pending": {}, "route": "review"}


async def intake(deps: TurnDeps, state: dict) -> dict:
    query, remembered = state["query"], state.get("repo") or ""
    if "diff --git " in query:
        return {"route": "pasted"}
    envelope = targets.envelope_input(query)
    target = targets.from_envelope(envelope, remembered) if envelope else targets.parse(query, remembered)
    pending = Target.from_dict(state.get("pending"))
    if target is None and pending is not None and targets.repo_in(query):
        target = targets.with_repo(pending, targets.repo_in(query))
    if target is None:
        if not deps.traceparent:
            return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
        answer = load_object(await ask_once(deps, ({"role": "system", "content": prompts.INTAKE_SYSTEM},
                                                   {"role": "user", "content": query}))) or {}
        if answer.get("status") == "off_topic":
            return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
        target = targets.from_model(answer, remembered) if answer.get("status") == "ok" else None
        if target is None:
            return {"reply": prompts.ASK_TARGET, "route": ""}
    if not target.has_repo:
        return {"reply": prompts.ASK_REPO.format(number=target.number or "?"), "pending": target.to_dict(),
                "route": ""}
    return _go(target)


def answer_ticket(state: dict) -> dict:
    """The answer to "which ticket?": the paused target, with the ticket (or the diff-only choice) set."""
    target = Target.from_dict(state.get("pending_ticket"))
    if target is None:
        return {"reply": prompts.NO_REPORT, "route": ""}
    chosen = targets.ticket_from_answer(state.get("query") or "")
    return {**_go(Target(**{**target.to_dict(), "ticket": chosen})), "pending_ticket": {}}


async def followup(deps: TurnDeps, state: dict) -> dict:
    review = Review.from_dict(state.get("report"))
    if review is None:
        return {"reply": prompts.NO_REPORT, "route": ""}
    query = state["query"]
    code = _in_code(review, query)
    if code is not None:
        return code
    if not deps.traceparent:
        return {"reply": prompts.NO_TRACE_CONTEXT, "route": ""}
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_report(_context(review))}"
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": query}))
    word = text.strip().strip("`.").strip()
    if word.startswith(prompts.OFF_TOPIC):
        return {"reply": smalltalk.OFF_TOPIC_REPLY, "route": ""}
    if word == prompts.POST:
        return {"route": "write"}
    if word == prompts.SHOW_CRITERIA:
        return {"reply": render.criteria_view(review), "route": ""}
    if word == prompts.SHOW_SIMILAR:
        return {"reply": render.similar_view(review), "route": ""}
    if word.startswith(prompts.FOCUS_PREFIX):
        return _focus(review, word[len(prompts.FOCUS_PREFIX):].strip().casefold())
    return {"reply": render.clean(text) or "I couldn't read that; ask again about the review.", "route": ""}


def _in_code(review: Review, query: str) -> dict | None:
    if _POST.match(query):
        return {"route": "write"}
    if _SKIP.match(query):
        return {"reply": "OK, nothing was posted.", "route": ""}
    if _SHOW_CRITERIA.match(query):
        return {"reply": render.criteria_view(review), "route": ""}
    if _SHOW_SIMILAR.match(query):
        return {"reply": render.similar_view(review), "route": ""}
    ticket = targets.ticket_in(query)
    if ticket and _TICKET_OVERRIDE.search(query) and review.number:
        return _go(Target(review.owner, review.repo, review.number, ticket=ticket, focus=review.focus))
    focus = targets.focus_in(query)
    if focus:
        return _focus(review, focus)
    return None


def _focus(review: Review, focus: str) -> dict:
    if focus not in targets.FOCUSES:
        return {"reply": "I can focus on security, tests or performance.", "route": ""}
    return {"reply": render.card(review, focus), "envelope": render.envelope(review, focus), "route": ""}


def _context(review: Review) -> str:
    searched = ", ".join(review.reviewed) or "none"
    text = f"{render.card(review)}\n\n{render.criteria_view(review)}\n\nFiles searched for evidence: {searched}"
    return text[:_MAX_FOLLOWUP_CHARS]


async def write(deps: TurnDeps, state: dict) -> dict:
    review = Review.from_dict(state.get("report"))
    if review is None:
        return {"reply": prompts.NO_REPORT, "route": ""}
    prepared = await writes.prepare_comment(deps, review, state.get("outcomes") or {})
    if prepared.proposal is None:
        return {"reply": prepared.reply, "route": ""}
    return {"reply": "", "proposal": prepared.proposal.to_dict(), "route": ""}
