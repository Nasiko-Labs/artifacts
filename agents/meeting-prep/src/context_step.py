"""One meeting: the context step (a bounded tool loop over read tools, its evidence post-checked in
code) and the card step (one tool-less call over the numbered evidence, citation-checked in code).

Budget per meeting: CONTEXT_LOOP_CALLS in the loop, one salvage call if the loop hits its limit, and
one card call."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import tzinfo

import cards
import evidence as evd
import prep_prompts as prompts
import timewin
from deps import TurnDeps
from events import Meeting, others
from nasiko_mcp import McpTool
from reader import ask_once, read_loop
from tool_health import ToolHealth
from verbatim import Seen

log = logging.getLogger("meeting_prep.context")

CONTEXT_LOOP_CALLS = 4


@dataclass(frozen=True)
class Prepared:
    """A meeting with its checked evidence and card; what is rendered and cached."""

    meeting: Meeting
    evidence: tuple[evd.Evidence, ...]
    card: cards.Card
    dropped: int = 0
    incomplete: bool = False

    def to_dict(self) -> dict:
        return {"meeting": self.meeting.to_dict(), "evidence": [e.to_dict() for e in self.evidence],
                "card": self.card.to_dict(), "dropped": self.dropped, "incomplete": self.incomplete}

    @staticmethod
    def from_dict(data: dict) -> Prepared:
        return Prepared(Meeting.from_dict(data["meeting"]), tuple(evd.Evidence.from_dict(e) for e in data["evidence"]),
                        cards.Card.from_dict(data["card"]), int(data.get("dropped") or 0),
                        bool(data.get("incomplete")))


def meeting_view(meeting: Meeting, me: str, tz: tzinfo) -> dict:
    """What the model is told about the meeting (as data, fenced)."""
    event = meeting.event
    return {
        "id": event.id, "series_id": event.series_id or None, "title": event.title,
        "local_time": f"{timewin.day_label(meeting.start.astimezone(tz).date())} "
                      f"{timewin.clock(meeting.start, tz)}-{timewin.clock(meeting.end, tz)}",
        "attendees": [{"email": a.email, "name": a.name} for a in others(event, me)],
        "organizer": event.organizer, "description_excerpt": event.description_excerpt, "links": list(event.links),
    }


async def prepare(deps: TurnDeps, tools: Sequence[McpTool], health: ToolHealth, meeting: Meeting, *,
                  me: str, tz: tzinfo) -> Prepared:
    window = timewin.evidence_window(meeting.start.astimezone(tz).date(), tz, deps.now)
    prior = timewin.prior_window(meeting.start)
    view = meeting_view(meeting, me, tz)
    raw, seen, hit_limit = await _gather(deps, tools, health, view, window, prior)
    checked = evd.check(raw, seen, tz=tz, window=window, prior=prior)
    card = await _card(deps, meeting, view, checked, me, tz)
    return Prepared(meeting, checked.items, card, checked.dropped + card.dropped, hit_limit)


async def _gather(deps: TurnDeps, tools: Sequence[McpTool], health: ToolHealth, view: dict,
                  window: timewin.Window, prior: timewin.Window) -> tuple[tuple[dict, ...], Seen, bool]:
    if not tools:
        return (), Seen(""), False
    system = prompts.context_system(window, prior)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": prompts.fence_meeting(view)})
    result, fenced = await read_loop(deps, messages, tools, health, CONTEXT_LOOP_CALLS)
    hit_limit = result.status == "max_iterations"
    text = result.text if result.status == "completed" else ""
    if hit_limit and fenced.seen_text:
        log.info("context step hit its step limit; salvaging what the tools returned")
        text = await ask_once(deps, (
            {"role": "system", "content": system},
            {"role": "user", "content": f"{prompts.fence_meeting(view)}\n\nTool results from this turn:\n"
                                        f"{prompts.fence(fenced.seen_text)}\n\n{prompts.SALVAGE_INSTRUCTION}"},
        ))
    return evd.parse_evidence(text) or (), Seen(fenced.seen_text), hit_limit


async def _card(deps: TurnDeps, meeting: Meeting, view: dict, checked: evd.Checked, me: str,
                tz: tzinfo) -> cards.Card:
    attendees = others(meeting.event, me)
    if not checked.items:  # nothing to cite: the card is the invite, no LLM call
        return cards.check(None, meeting, attendees, (), None, deps.now)
    last = timewin.short_date(checked.last_meeting_at, tz) if checked.last_meeting_at else "none found"
    user = (f"{prompts.fence_meeting(view)}\n\n{prompts.fence_evidence(evd.describe(checked.items, tz))}\n\n"
            f"Previous meeting: {last}")
    text = await ask_once(deps, ({"role": "system", "content": prompts.card_system()},
                                 {"role": "user", "content": user}))
    return cards.check(cards.parse_card(text), meeting, attendees, checked.items, checked.last_meeting_at, deps.now)
