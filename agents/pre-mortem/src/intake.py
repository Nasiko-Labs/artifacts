"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                               -> canned intro (no call)
    a pre-mortem stored, short message ─┬─ follow-up    -> followup
                                        └─ otherwise    -> scope check (decline or follow-up help)
    plan over 30,000 chars                              -> pause: first / last ~5k words / cancel
    plan of 80+ words                                   -> run
    short, asks for a pre-mortem                        -> pause: paste the plan (no call)
    short, anything else                                -> scope check (decline, or pause for the plan)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import hitl
import plan as plan_text
import prompts
import smalltalk
from inputs import Incoming, split_request, word_count
from rank import clamp_count

_ASKS_FOR_PREMORTEM = re.compile(r"pre-?mortem|failure modes?|what could go wrong", re.IGNORECASE)
_COUNT = re.compile(r"\btop (\d{1,2})\b|\b(\d{1,2}) (?:stories|failures?|failure modes|ways|risks)\b", re.IGNORECASE)
_FOCUS = (
    ("pragmatist", re.compile(r"deliver|schedul|capacity|sequenc|dependenc|deadline", re.IGNORECASE)),
    ("skeptic", re.compile(r"premise|assumption", re.IGNORECASE)),
    ("critic", re.compile(r"downside|adoption|worst|harm|edge case|customer", re.IGNORECASE)),
    ("architect", re.compile(r"design|architect|tech(?:nical)? debt|longevity|ownership|scope", re.IGNORECASE)),
)
_FOCUS_ON = re.compile(r"\bfocus(?:ed|ing)? on\b(.*)", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                               # reply | pause | run | followup | scope
    reply: str = ""
    pending: hitl.Pending | None = None
    plan: str = ""
    context: tuple[str, ...] = ()
    count: int = 7
    focus: str = ""
    command: followup.Command | None = None
    notes: tuple[str, ...] = ()


def options(instruction: str) -> tuple[int, str]:
    """(story count 5-7, focus lens or "") from the user's request line."""
    match = _COUNT.search(instruction or "")
    count = clamp_count(int(match.group(1) or match.group(2)) if match else None)
    focus_on = _FOCUS_ON.search(instruction or "")
    focus = next((lens for lens, rx in _FOCUS if focus_on and rx.search(focus_on.group(1))), "")
    return count, focus


def plan_turn(incoming: Incoming, has_result: bool) -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    request = split_request(incoming.text)
    body = prompts.strip_markers(request.plan)
    context = tuple(prompts.strip_markers(c) for c in request.context)
    words = word_count(body)
    if has_result and words < hitl.MIN_PLAN_WORDS:
        command = followup.parse(incoming.text)
        return Route("followup", command=command) if command else Route("scope")
    if len(body) > hitl.MAX_PLAN_CHARS:
        pending = hitl.Pending(hitl.TOO_BIG, request.instruction, context,
                               first=plan_text.cut_first(body, hitl.MAX_PLAN_CHARS),
                               last=plan_text.cut_last(body, hitl.MAX_PLAN_CHARS), words=words,
                               wants_json=incoming.wants_json)
        return Route("pause", pending=pending)
    if words >= hitl.MIN_PLAN_WORDS:
        count, focus = options(request.instruction)
        return Route("run", plan=body, context=context, count=count, focus=focus)
    if _ASKS_FOR_PREMORTEM.search(incoming.text):
        return Route("pause", pending=need_plan(incoming, request.instruction or body, context))
    return Route("scope")


def need_plan(incoming: Incoming, instruction: str, context: tuple[str, ...] = ()) -> hitl.Pending:
    return hitl.Pending(hitl.NEED_PLAN, prompts.strip_markers(instruction)[:300], context,
                        wants_json=incoming.wants_json)


def plan_resume(answer: hitl.Answer, pending: hitl.Pending) -> Route:
    if answer.action != "run":
        return Route("reply", answer.reply or prompts.CANCELLED)
    count, focus = options(answer.instruction)
    notes: tuple[str, ...] = ()
    if pending.kind == hitl.TOO_BIG and answer.plan in (pending.first, pending.last):
        part = "first" if answer.plan == pending.first else "last"
        notes = (f"_The plan was about {pending.words:,} words; I used the {part} ~5k words._",)
    return Route("run", plan=answer.plan, context=answer.context, count=count, focus=focus, notes=notes)
