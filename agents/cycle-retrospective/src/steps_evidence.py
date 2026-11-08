"""The evidence step: for the largest spilled issues only, their comments and (when chat is reachable) chat
messages naming them, read by a bounded tool loop. Every text kept is verbatim in this turn's tool output."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import checks
import coverage
import prompts
import records
import replies
from dates import day
from deps import TurnDeps
from discovery import discover
from reader import TurnReader
from records import Cycle, Evidence, Issue

EVIDENCE_LOOP_CALLS = 5  # plus one JSON repair = 6
EVIDENCE_TOOL_CALLS = 20


@dataclass(frozen=True)
class EvidenceOutcome:
    evidence: tuple[Evidence, ...] = ()
    chat_searched: bool = False
    comments_read: bool = False
    extracted: int = 0
    dropped: int = 0
    salvaged: bool = False
    notes: tuple[str, ...] = ()


async def evidence_step(deps: TurnDeps, turn: TurnReader, issues: Sequence[Issue], cycle: Cycle,
                        channels: Sequence[str]) -> EvidenceOutcome:
    if not issues:
        return EvidenceOutcome(chat_searched=True, comments_read=True)
    catalog, failure = await discover(deps, "evidence")
    if failure is not None:
        return EvidenceOutcome(notes=(failure,))
    tools = catalog.read_tools()
    tracker = coverage.assess("tracker", tools, catalog.source_of)
    chat = _chat(tools, catalog.source_of)
    if not tracker.ok and not chat.ok:
        return EvidenceOutcome(notes=(coverage.line(tracker), replies.CHAT_NOT_SEARCHED))
    ids = [i.id for i in issues]
    system = prompts.evidence_system(ids, day(cycle.starts_at), day(cycle.ends_at) or deps.today.isoformat(),
                                     channels, chat.ok)
    run = await turn.run_json(deps, tools, system, "Find why these issues were not finished: " + ", ".join(ids),
                              EVIDENCE_LOOP_CALLS, records.parse_evidence, EVIDENCE_TOOL_CALLS)
    tracker = coverage.assess("tracker", tools, catalog.source_of, turn.health.dropped)
    chat = _chat(tools, catalog.source_of, turn.health.dropped)
    notes = list(run.notes)
    if not chat.ok:
        notes += [line for line in (coverage.line(chat),) if line and chat.state == "unavailable"]
        notes.append(replies.CHAT_NOT_SEARCHED)
    if not tracker.ok:
        notes.append(coverage.line(tracker))
    if run.status == "paused":
        notes.append(replies.READ_NEEDS_APPROVAL)
    found = run.parsed or ()
    checked = checks.check_evidence(found, turn.seen_text, ids)
    return EvidenceOutcome(checked.kept, chat.ok, tracker.ok, len(found), checked.dropped,
                           run.status == "max_iterations", tuple(notes))


def _chat(tools, source_of, dropped=None) -> coverage.SourceStatus:
    """Chat is optional, so only a tool placed with chat counts; an unplaced tool is not assumed to be chat."""
    if not any(source_of(t) == "chat" for t in tools):
        return coverage.SourceStatus("chat", "missing")
    return coverage.assess("chat", tools, source_of, dropped)
