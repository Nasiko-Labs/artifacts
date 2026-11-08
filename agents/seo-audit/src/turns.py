"""Pure: decide what one turn does before any model call or fetch.

    greeting / small talk                    -> canned intro (no call)
    an earlier audit's envelope (JSON)       -> keep it as the "previous audit"
    pasted HTML                              -> audit (diffed against the stored audit on "again")
    one URL                                  -> guarded fetch, then audit
    a follow-up command, audit stored        -> answer from the stored audit (no call; a title rewrite is 1)
    a short "audit my page" with no page     -> pause: what should I audit?
    anything else                            -> one scope call (off-topic decline / pause / help)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import followup
import hitl
import prompts
import smalltalk
from inputs import Incoming, Request, parse_request

SHORT_ASK_MAX_WORDS = 12
_AUDIT_ASK = re.compile(r"\b(?:audit|seo|check|review|analy[sz]e|score|grade)\b", re.IGNORECASE)
_OFF_TOPIC_HINT = re.compile(r"\b(?:ranking|rank|backlinks?|traffic|search volume|blog post|ad copy)\b",
                             re.IGNORECASE)


@dataclass(frozen=True)
class Plan:
    route: str            # reply | pause | audit | fetch | followup | classify | envelope
    reply: str = ""
    pending: hitl.Pending | None = None
    request: Request | None = None
    command: followup.Command | None = None
    compare: bool = False


def plan_turn(incoming: Incoming, has_audit: bool) -> Plan:
    canned = smalltalk.reply_to(incoming.text)
    if canned is not None:
        return Plan("reply", canned)
    req = parse_request(incoming)
    if req.envelope is not None:
        return Plan("envelope", request=req)
    compare = has_audit and followup.asks_for_comparison(req.instruction)
    if req.html:
        return Plan("audit", request=req, compare=compare)
    if req.url:
        return Plan("fetch", request=req, compare=compare)
    if has_audit:
        command = followup.parse_command(incoming.text)
        if command is not None:
            return Plan("followup", request=req, command=command)
    words = len(incoming.text.split())
    if _AUDIT_ASK.search(incoming.text) and not _OFF_TOPIC_HINT.search(incoming.text) and words <= SHORT_ASK_MAX_WORDS \
            and not has_audit:
        return Plan("pause", pending=hitl.Pending(keyword=req.keyword, instruction=incoming.text[:300]))
    return Plan("classify", request=req)


def plan_resume(pending: hitl.Pending, raw_answer: str, has_audit: bool) -> Plan:
    answer = hitl.interpret(pending, raw_answer)
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if answer.action == "ask":
        return Plan("pause", pending=answer.pending)
    plan = plan_turn(Incoming(text=answer.text), has_audit)
    if plan.route in ("audit", "fetch") and pending.keyword and not plan.request.keyword:
        return replace(plan, request=replace(plan.request, keyword=pending.keyword))
    return plan
