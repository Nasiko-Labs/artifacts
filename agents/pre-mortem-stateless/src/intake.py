"""Pure: decide what one turn does, before any LLM call. Nothing ever pauses: every route ends the task.

    greeting / small talk                                    -> canned intro (no call)
    a plan or pre-mortem in history, short ─┬─ follow-up     -> followup
                                            ├─ asks for one  -> run again on the plan from history
                                            └─ otherwise     -> scope check (decline or follow-up help)
    plan over 30,000 chars                                   -> run on the first ~5k words, say what was left out
    plan of 80+ words                                        -> run
    short, a follow-up command, no pre-mortem in history     -> "no pre-mortem yet", or "paste it again" when
                                                                history may have been trimmed (no call)
    short plan pasted with a pre-mortem request              -> "too short, at least 80 words" (no call)
    short, asks for a pre-mortem                             -> ask for the plan (no call)
    short, anything else                                     -> scope check (decline, or ask for the plan)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import plan as plan_text
import prompts
import smalltalk
from history import ASSISTANT
from inputs import Incoming, split_request, word_count
from rank import clamp_count
from recall import MAX_PLAN_CHARS, MIN_PLAN_WORDS, Prior

_ASKS_FOR_PREMORTEM = re.compile(r"pre-?mortem|failure modes?|what could go wrong", re.IGNORECASE)
_COUNT = re.compile(r"\btop (\d{1,2})\b|\b(\d{1,2}) (?:stories|failures?|failure modes|ways|risks)\b", re.IGNORECASE)
_FOCUS = (
    ("pragmatist", re.compile(r"deliver|schedul|capacity|sequenc|dependenc|deadline", re.IGNORECASE)),
    ("skeptic", re.compile(r"premise|assumption", re.IGNORECASE)),
    ("critic", re.compile(r"downside|adoption|worst|harm|edge case|customer", re.IGNORECASE)),
    ("architect", re.compile(r"design|architect|tech(?:nical)? debt|longevity|ownership|scope", re.IGNORECASE)),
)
_DEEP_ON_RESULT = re.compile(r"\b(?:deeper|dig|drill)\b|\b(?:failure|story|risk)\b", re.IGNORECASE)
_FOCUS_ON = re.compile(r"\bfocus(?:ed|ing)? on\b(.*)", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                               # reply | run | followup | scope
    reply: str = ""
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


def plan_turn(incoming: Incoming, prior: Prior) -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    request = split_request(incoming.text)
    body = prompts.strip_markers(request.plan)
    context = tuple(prompts.strip_markers(c) for c in request.context)
    words = word_count(body)
    if (prior.plan or prior.envelope) and words < MIN_PLAN_WORDS:
        command = followup.parse(incoming.text)
        if command:
            return Route("followup", command=command)
        if prior.plan and _ASKS_FOR_PREMORTEM.search(incoming.text):
            return _from_history(prior, request.instruction or incoming.text, context)
        return Route("scope")
    if len(body) > MAX_PLAN_CHARS:
        return _first_part(body, words, request.instruction, context)
    if words >= MIN_PLAN_WORDS:
        count, focus = options(request.instruction)
        return Route("run", plan=body, context=context, count=count, focus=focus)
    if _ASKS_FOR_PREMORTEM.search(incoming.text):
        return Route("reply", ask_for_plan(incoming))
    if _is_result_view(incoming.text):  # a command on a pre-mortem that is not in this turn's history
        trimmed = _premortem_may_be_trimmed(incoming)
        return Route("reply", prompts.NOT_IN_HISTORY if trimmed else prompts.NO_PREMORTEM_YET)
    return Route("scope")


def ask_for_plan(incoming: Incoming) -> str:
    """No usable plan. Pasted but short -> too short (with the minimum); nothing pasted and earlier turns that
    may have held it -> it was trimmed from what the platform sent; otherwise -> paste one."""
    body = prompts.strip_markers(split_request(incoming.text).plan)
    if body.strip() and not _ASKS_FOR_PREMORTEM.search(body):  # plan text, not just "pre-mortem my plan"
        return prompts.too_short(word_count(body))
    return prompts.NOT_IN_HISTORY if _premortem_may_be_trimmed(incoming) else prompts.NEED_PLAN


def _is_result_view(text: str) -> bool:
    """A follow-up that only makes sense on a pre-mortem ("go deeper on failure 1", "show the plan lines",
    "as json"). Loose matches a general question can share ("top 5 movies", "more on 2 things") go to the
    scope check instead."""
    command = followup.parse(text)
    if command is None or command.kind == "top":
        return False
    return command.kind != "deep" or bool(_DEEP_ON_RESULT.search(text))


def _premortem_may_be_trimmed(incoming: Incoming) -> bool:
    """True when history may have lost a plan or pre-mortem; False when it plainly shows none was made yet
    (no earlier turns, or the newest reply is one of this agent's own no-result replies)."""
    replies = [t.text for t in incoming.history if t.role == ASSISTANT]
    if not incoming.history:
        return False
    if not replies:
        return True
    return not any(marker in replies[-1] for marker in prompts.NO_RESULT_MARKERS)


def _from_history(prior: Prior, instruction: str, context: tuple[str, ...]) -> Route:
    """A short "pre-mortem it again, focus on delivery": the newest plan in history is the sensible default."""
    count, focus = options(instruction)
    return Route("run", plan=prior.plan.text, context=(*prior.plan.context, *context), count=count, focus=focus,
                 notes=("_I used the plan from earlier in our conversation._",))


def _first_part(body: str, words: int, instruction: str, context: tuple[str, ...]) -> Route:
    """Too big to read at once: the first ~5k words, and a clear note on what was left out."""
    kept = plan_text.cut_first(body, MAX_PLAN_CHARS)
    total, used = len(plan_text.number_lines(body)), len(plan_text.number_lines(kept))
    count, focus = options(instruction)
    note = (f"_The plan was about {words:,} words; I can read about 5k words (30,000 characters) at once, so I "
            f"used the first ~5k words (lines 1-{used} of {total}) and left out the rest. Paste a later section "
            "on its own to pre-mortem it._")
    return Route("run", plan=kept, context=context, count=count, focus=focus, notes=(note,))
