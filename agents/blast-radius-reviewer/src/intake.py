"""Pure: which route a turn takes, decided in code before any model call.

    greeting / thanks / "what can you do"   -> reply (INTRO, zero calls)
    a unified diff (or an envelope with one) -> run    (parse, census, probes, judge, merge)
    after a result: "as json"               -> json   (re-render the stored envelope)
    after a result: text naming a flagged token -> rejudge (only the items whose token it mentions)
    asks for a review but no diff           -> pause  (Q1 "Paste the diff")
    anything else                           -> scope  (one classify call: decline or ask)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import hitl
import merge
import prompts
import smalltalk
from diff_parse import looks_like_diff
from inputs import Incoming, Request, split_request

_ASKS_FOR_REVIEW = re.compile(r"\b(?:diff|patch|blast radius|what could break|could break|break(?:s|ing)? change|"
                              r"pull request|\bPR\b|migration|review (?:this|my) change)", re.IGNORECASE)
_JSON_ONLY = re.compile(r"^\W*(?:(?:show|give|return)(?: it| me| the result| this)?\s*)?(?:as json|json only|"
                        r"in json)\W*$", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                     # reply | run | json | rejudge | pause | scope
    reply: str = ""
    request: Request | None = None
    ids: tuple[str, ...] = ()
    added_context: str = ""
    pending: hitl.Pending | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)


def plan_turn(incoming: Incoming, fired: list[dict] | None) -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    request = split_request(incoming.text)
    if looks_like_diff(request.diff):
        return Route("run", request=request, notes=incoming.notes)
    if fired is not None:
        if _JSON_ONLY.match(incoming.text.strip()):
            return Route("json")
        added = prompts.strip_markers(incoming.text)
        ids = tuple(merge.tokens_in(added, fired))
        return Route("rejudge", ids=ids, added_context=added) if ids else Route("scope")
    if _ASKS_FOR_REVIEW.search(incoming.text):
        return Route("pause", pending=hitl.Pending(wants_json=incoming.wants_json))
    return Route("scope")


def plan_resume(answer: hitl.Answer, incoming: Incoming) -> Route:
    if answer.action != "run":
        return Route("reply", answer.reply or prompts.CANCELLED)
    return Route("run", request=split_request(answer.text), notes=incoming.notes)
