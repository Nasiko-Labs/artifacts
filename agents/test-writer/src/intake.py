"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk                          -> canned intro (no call)
    source code pasted ─┬─ unsupported language    -> fixed reply (no call)
                        └─ otherwise               -> run
    no code, stored tests                          -> follow-up (json / more / revise)
    no code, asks for tests                        -> pause: paste the code (no call)
    no code, anything else                         -> scope check (one call: decline, or pause)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import hitl
import lang
import prompts
import request
import smalltalk
from census import supported, turn_language
from inputs import Incoming, from_envelope
from lang import SourceFile

_ASKS_FOR_TESTS = re.compile(r"\btests?\b|\bpytest\b|\bjest\b|\bvitest\b|\bunittest\b|\bunit[- ]tests?\b|"
                             r"\bedge cases?\b|\btest cases?\b", re.IGNORECASE)


@dataclass(frozen=True)
class Route:
    kind: str                                   # reply | pause | run | followup | scope
    reply: str = ""
    pending: hitl.Pending | None = None
    files: tuple[SourceFile, ...] = ()
    instruction: str = ""
    existing: str = ""
    command: followup.Command | None = None


def plan_turn(incoming: Incoming, stored: dict) -> Route:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Route("reply", canned)
    route = code_route(incoming.text)
    if route is not None:
        return route
    if stored.get("files"):
        known = list(stored.get("known") or ())
        command = followup.parse(incoming.text, known, list(stored.get("selected") or ()),
                                 list(stored.get("remaining") or ()))
        return Route("followup", command=command)
    if _ASKS_FOR_TESTS.search(incoming.text):
        return Route("pause", pending=need_code(incoming))
    return Route("scope")


def code_route(text: str) -> Route | None:
    """A run when the text holds source code (or an unsupported-language reply); None when it holds none."""
    source = from_envelope(text)
    extracted = lang.extract(source if source is not None else text)
    if not extracted.files:
        return None
    language = turn_language(extracted.files)
    if not supported(language):
        return Route("reply", prompts.unsupported_reply(language))
    return Route("run", files=extracted.files, instruction=extracted.instruction, existing=extracted.existing_tests)


def need_code(incoming: Incoming) -> hitl.Pending:
    return hitl.Pending(hitl.NEED_CODE, " ".join(incoming.text.split())[:500], incoming.wants_json)


def plan_resume(answer: hitl.Answer) -> Route:
    if answer.action != "run":
        return Route("reply", answer.reply or prompts.CANCELLED)
    return code_route(answer.text) or Route("reply", prompts.NOT_CODE)


def options(route: Route, lang_name: str) -> tuple[str, str, tuple[str, ...], tuple[str, ...], str]:
    """(framework, framework note, explicit targets, loose targets, module override) for a run."""
    framework, note = request.framework_for(lang_name, route.instruction, route.existing)
    explicit, loose = request.targets_in(route.instruction)
    return framework, note, explicit, loose, request.module_in(route.instruction)
