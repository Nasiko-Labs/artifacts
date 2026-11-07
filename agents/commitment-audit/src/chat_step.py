"""The chat step: one bounded tool loop searching chat in the quarter for every commitment, its
signals post-checked (evidence.check_signals). It never ends the turn: when chat cannot be read, or
time is short, the audit goes on with a note and lower confidence."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import coverage
import evidence
import prompts
from commitments import Commitment, listing
from deps import TurnDeps
from discovery import RECOVER_META, discover
from quarter import Quarter
from reader import Reader

log = logging.getLogger("commitment_audit.chat")

LOOP_CALLS = 4  # plus one repair = 5
SKIP_AFTER_SECS = 380.0


@dataclass(frozen=True)
class ChatOutcome:
    searched: bool  # chat was read successfully: counts toward confidence
    signals: dict[str, tuple[evidence.Signal, ...]] = field(default_factory=dict)
    notes: tuple[str, ...] = ()


async def collect(deps: TurnDeps, reader: Reader, items: Sequence[Commitment], quarter: Quarter) -> ChatOutcome:
    if deps.elapsed() >= SKIP_AFTER_SECS:
        log.info("skipping chat: %.0fs of the flow window already used", deps.elapsed())
        return ChatOutcome(False, notes=(prompts.CHAT_TIME_NOTE,))
    catalog, failure = await discover(deps, "chat")
    if failure is not None:
        return ChatOutcome(False, notes=(coverage.note(coverage.StepCoverage("chat", "missing")),))
    tools = catalog.read_tools(("chat",))
    if not tools:
        return ChatOutcome(False, notes=(coverage.note(coverage.StepCoverage("chat", "missing")),))
    user = prompts.fence_items(json.dumps(listing(items), ensure_ascii=False, indent=1))
    step = await reader.run(deps, prompts.chat_system(quarter, deps.today), user, tools, LOOP_CALLS,
                            lambda text: evidence.parse_items(text, "signals"))
    covered = coverage.assess("chat", tools, reader.health.dropped, frozenset({RECOVER_META}))
    if not covered.ok:
        return ChatOutcome(False, notes=(coverage.note(covered),))
    if step.paused or step.parsed is None:
        return ChatOutcome(False, notes=(prompts.STEP_LIMIT_NOTE,) if step.hit_limit else ())
    signals = evidence.check_signals(step.parsed, items, reader.seen_text, quarter, deps.today)
    return ChatOutcome(True, signals, (prompts.STEP_LIMIT_NOTE,) if step.hit_limit else ())
