"""Follow-ups on a list already shown: decided in code where the words are plain ("continue", "show
ignored", "move #3 to urgent", "why #2", "Dana is a VIP"), else by one model call that answers with a
sentinel (CONTINUE | WHY:<n> | MOVE:<n>:<bucket> | SHOW_IGNORED | RETRIAGE | READ_ONLY | OFF_TOPIC) or a
short answer about the list. Moving a thread changes only the stored bucket; nothing touches the mailbox."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import parse
import paste
import prompts
import render
import smalltalk
import triage
from classify import ask_once
from deps import TurnDeps
from models import Checks, Mail
from view import mails_of, reply_only, shown, threads_of

_WRITE = re.compile(
    r"\b(?:archive|delete|trash|unsubscribe|forward|snooze)\b|\blabel (?:them|these|those|it|all|the)\b|"
    r"\bmark\b.{0,40}\b(?:as )?(?:read|done|spam)\b|\bsend\b|\bdraft\b|\breply to (?:them|it|this|that|#?\d+|all)\b",
    re.IGNORECASE)
_TRIAGE_WORDS = re.compile(
    r"\b(?:inbox|e-?mails?|mail|triage|urgent|unread|threads?|messages?|repl(?:y|ies)|ignored?|vips?|priorit\w*|"
    r"to-?do|need(?:s)? me|waiting on me)\b", re.IGNORECASE)
_RETRIAGE = re.compile(r"\b(?:triage|re-?triage|redo|again|refresh|re-?check|recheck|re-?sort|resort)\b|\binbox\b",
                       re.IGNORECASE)
_TRIAGE_CUE = re.compile(r"\btriage\b|\binbox\b", re.IGNORECASE)
_TO_TRIAGE = {"route": "triage", "reply": "", "envelope": None}
_SHOW_IGNORED = re.compile(r"\bshow (?:me )?(?:the )?(?:ignored?|ignore list)\b|\b(?:list|expand) (?:the )?ignored?\b",
                           re.IGNORECASE)
_MOVE = re.compile(r"\bmove #?(\d{1,3}) to (urgent|reply|ignore)\b", re.IGNORECASE)
_WHY = re.compile(r"\bwhy\b.*?#(\d{1,3})\b", re.IGNORECASE)
_SENTINEL_MOVE = re.compile(r"^MOVE:\s*(\d{1,3})\s*:\s*(urgent|reply|ignore)\b", re.IGNORECASE)
_SENTINEL_WHY = re.compile(r"^WHY:\s*(\d{1,3})\b", re.IGNORECASE)
_DIGITS = re.compile(r"\d+")
_MAX_ANSWER_CHARS = 600
HELP_TEXT = ("I can explain a thread (\"why #2\"), move one (\"move #3 to urgent\"), show the ignored ones "
             "(\"show ignored\"), continue with older threads, or triage again (\"last 3 days\").")


def is_write_request(text: str) -> bool:
    return bool(_WRITE.search(text or ""))


def looks_like_triage(text: str, request: parse.Request) -> bool:
    return bool(_TRIAGE_WORDS.search(text or "") or request.sender or request.vips or request.window_given
                or request.from_envelope)


async def handle(state: Mapping[str, Any], deps: TurnDeps) -> dict:
    query = state["query"]
    request = parse.parse_request(query, deps.today, bool(state.get("wants_json")))
    if is_write_request(query):
        return reply_only(prompts.READ_ONLY_REPLY)
    plain = await _plain(state, query, request, deps)
    if plain is not None:
        return plain
    if not deps.traceparent:
        return reply_only(prompts.NO_TRACE_CONTEXT)
    listing = render.render_for_model(threads_of(state))
    system = f"{prompts.FOLLOWUP_SYSTEM}\n\n{prompts.fence_prior(listing)}"
    answer = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": query}))
    return await _dispatch(state, answer, listing, deps, request)


async def _plain(state: Mapping[str, Any], query: str, request: parse.Request, deps: TurnDeps) -> dict | None:
    """The follow-up decided in code, or None for the model to read."""
    if request.is_continue:
        return await continue_slice(state, request, deps)
    if _SHOW_IGNORED.search(query):
        return reply_only(render.render_ignored(threads_of(state)))
    move = _MOVE.search(query)
    if move:
        return move_to(state, int(move.group(1)), move.group(2).casefold(), request)
    why = _WHY.search(query)
    if why:
        return explain(state, int(why.group(1)))
    retriage = bool(request.window_given or request.sender or request.from_envelope or _TRIAGE_CUE.search(query))
    if request.vips and not retriage:
        return vip_rerun(state, request, deps)
    if retriage or _RETRIAGE.search(query) or paste.is_paste(query):
        return _TO_TRIAGE
    return None


async def _dispatch(state: Mapping[str, Any], answer: str, listing: str, deps: TurnDeps,
                    request: parse.Request) -> dict:
    head = answer.strip()
    if head.startswith(prompts.OFF_TOPIC):
        return reply_only(smalltalk.OFF_TOPIC_REPLY)
    if head.startswith(prompts.READ_ONLY):
        return reply_only(prompts.READ_ONLY_REPLY)
    if head.startswith(prompts.CONTINUE):
        return await continue_slice(state, request, deps)
    if head.startswith(prompts.SHOW_IGNORED):
        return reply_only(render.render_ignored(threads_of(state)))
    if head.startswith(prompts.RETRIAGE):
        return _TO_TRIAGE
    move = _SENTINEL_MOVE.match(head)
    if move:
        return move_to(state, int(move.group(1)), move.group(2).casefold(), request)
    why = _SENTINEL_WHY.match(head)
    if why:
        return explain(state, int(why.group(1)))
    return reply_only(_checked_answer(head, listing))


def _checked_answer(answer: str, listing: str) -> str:
    """A free answer about the list, kept only when every number in it is in the list."""
    text = render.safe(answer, _MAX_ANSWER_CHARS)
    if not text or any(d not in listing for d in _DIGITS.findall(text)):
        return HELP_TEXT
    return text


def explain(state: Mapping[str, Any], number: int) -> dict:
    threads = threads_of(state)
    if not 1 <= number <= len(threads):
        return reply_only(prompts.NOTHING_TO_EXPLAIN)
    return reply_only(render.render_why(number, threads[number - 1]))


def move_to(state: Mapping[str, Any], number: int, bucket: str, request: parse.Request) -> dict:
    """Re-bucket one thread in the stored list only; numbers stay as shown."""
    threads = threads_of(state)
    if not 1 <= number <= len(threads):
        return reply_only(prompts.NOTHING_TO_EXPLAIN)
    moved = [t.moved(bucket) if i == number else t for i, t in enumerate(threads, start=1)]
    lead = f"Moved #{number} to {bucket} for this conversation (nothing changed in your mailbox)."
    return shown(moved, render.Meta.from_dict(state.get("meta")), Checks.from_dict(state.get("checks")),
                 request.wants_json, lead)


def vip_rerun(state: Mapping[str, Any], request: parse.Request, deps: TurnDeps) -> dict:
    """New VIPs re-applied to the threads already read: rules only, no tool or model call."""
    vips = list(dict.fromkeys([*(state.get("vips") or ()), *request.vips]))
    threads = triage.rerun(deps, threads_of(state), mails_of(state), vips, tuple(state.get("me") or ()))
    lead = f"Got it: I'll treat {', '.join(request.vips)} as VIP for this conversation."
    out = shown(threads, render.Meta.from_dict(state.get("meta")), Checks.from_dict(state.get("checks")),
                request.wants_json, lead)
    return {**out, "vips": vips}


async def continue_slice(state: Mapping[str, Any], request: parse.Request, deps: TurnDeps) -> dict:
    """The next older slice, from the checkpointer (no new search)."""
    backlog = [Mail.from_dict(m) for m in state.get("backlog") or ()]
    if not backlog:
        return reply_only(prompts.NOTHING_TO_CONTINUE)
    if not deps.traceparent:
        return reply_only(prompts.NO_TRACE_CONTEXT)
    head, rest = backlog[:triage.THREAD_CAP], backlog[triage.THREAD_CAP:]
    old = render.Meta.from_dict(state.get("meta"))
    vips, me = tuple(state.get("vips") or ()), tuple(state.get("me") or ())
    outcome = await triage.run(deps, head, vips, me, capped=bool(rest))
    shown_before = len(state.get("threads") or ()) + old.already_answered
    meta = render.Meta(label=old.label, total=old.total, offset=old.offset + shown_before,
                       already_answered=outcome.already_answered)
    out = shown(outcome.threads, meta, outcome.checks, request.wants_json, "Older threads:")
    mails = {**dict(state.get("mails") or {}), **{k: m.to_dict() for k, m in outcome.mails.items()}}
    return {**out, "mails": mails, "backlog": [m.to_dict() for m in rest]}
