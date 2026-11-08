"""Pure: decide what one turn does, before any LLM call.

    greeting / small talk (no file, no image)     → canned intro (no call)
    a table, a table file or an envelope          → narrate it (≤ 2 calls)
    an image and no table                         → "I can't see images" (no call)
    a story stored                                → follow-up (followup.py: 0 or 1 call)
    nothing stored ─┬─ asks about a table/chart   → pause: paste the table (no call)
                    └─ anything else              → one scope-checked call (declined, or pause)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import followup
import hitl
import prompts
import smalltalk
from handoff import Source, find_source
from inputs import Incoming

TEXT_MAX_CHARS = 2000
_TABLE_ASK = re.compile(r"\b(?:table|chart|graph|numbers|data|figures|spreadsheet|csv|narrat\w*|story|"
                        r"explain (?:this|these|my)|summari[sz]e (?:this|these|my))\b", re.IGNORECASE)


@dataclass(frozen=True)
class Plan:
    route: str                       # reply | pause | narrate | follow | scope
    reply: str = ""
    pending: hitl.Pending | None = None
    source: Source | None = None
    command: str = ""                # follow: headline | longer | numbers | json | rewrite | question
    text: str = ""
    notes: tuple[str, ...] = ()


def plan_turn(incoming: Incoming, has_story: bool) -> Plan:
    if not incoming.files and not incoming.has_image:
        canned = smalltalk.reply_to(incoming.text)
        if canned is not None:
            return Plan("reply", canned, notes=incoming.notes)
    source = find_source(incoming.text, incoming.files)
    if source is not None:
        return Plan("narrate", source=source, notes=incoming.notes)
    if incoming.has_image:
        return Plan("reply", prompts.IMAGE_REPLY, notes=incoming.notes)
    text = prompts.strip_markers(incoming.text).strip()[:TEXT_MAX_CHARS]
    if has_story:
        return Plan("follow", command=followup.parse(text), text=text, notes=incoming.notes)
    if _TABLE_ASK.search(text):
        return Plan("pause", pending=hitl.Pending(hitl.NEED_SOURCE, question=text), notes=incoming.notes)
    return Plan("scope", text=text, notes=incoming.notes)


def plan_resume(answer: hitl.Answer) -> Plan:
    if answer.action == "cancel":
        return Plan("reply", prompts.CANCELLED)
    if answer.action == "give_up":
        return Plan("reply", prompts.NO_SOURCE_GIVEN_UP)
    if answer.action == "reask":
        return Plan("pause", pending=answer.pending)
    source = find_source(answer.text)
    if source is None:
        return Plan("reply", prompts.NO_SOURCE_GIVEN_UP)
    joined = "\n".join(x for x in (answer.question, source.instruction) if x.strip())
    return Plan("narrate", source=Source(joined, source.records, source.facts))
