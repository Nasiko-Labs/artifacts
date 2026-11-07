"""One read step, the same for code host, chat and docs: a bounded tool loop with its own JSON contract,
then (when the answer is not that JSON, or the step hit its limit) one tool-less repair call over the
step's fenced tool output. A step never ends the turn: when it cannot be read it returns a coverage note."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import coverage
import prompts
from deps import TurnDeps
from reader import BudgetExceeded, Reader, ask_once
from verify import parse_object

log = logging.getLogger("who_knows.steps")

_PAUSED = ("approval_pending", "authorization_required")


@dataclass(frozen=True)
class StepAnswer:
    """`data` is the parsed JSON (None when the step could not be read, with the reason in `note`);
    `seen` is this step's raw tool output, for the verbatim checks."""

    data: dict | None
    seen: str = ""
    note: str = ""
    salvaged: bool = False


async def run(deps: TurnDeps, reader: Reader, step: str, system: str) -> StepAnswer:
    before = reader.status(step)
    if before.state != "ok":
        return StepAnswer(None, note=coverage.reason(before))
    messages = ({"role": "system", "content": system},
                {"role": "user", "content": "Gather the evidence now, then reply with the JSON object only."})
    mark = reader.fenced.mark()
    result = await reader.loop(deps, step, messages)
    seen = reader.fenced.seen_since(mark)
    after = reader.status(step)
    if after.state != "ok":
        return StepAnswer(None, seen, coverage.reason(after))
    if result.status in _PAUSED:
        return StepAnswer(None, seen, prompts.READ_NEEDS_APPROVAL)
    hit_limit = result.status == "max_iterations"
    data = None if hit_limit else parse_object(result.text)
    if data is not None:
        return StepAnswer(data, seen)
    if not seen:
        return StepAnswer(None, seen, prompts.NOT_READABLE)
    repaired = await _repair(deps, reader, system, "" if hit_limit else result.text, seen)
    return StepAnswer(repaired, seen, "" if repaired is not None else prompts.NOT_READABLE, salvaged=True)


async def _repair(deps: TurnDeps, reader: Reader, system: str, answer: str, seen: str) -> dict | None:
    """One tool-less call that restates the step's findings as JSON from its (fenced) tool output."""
    log.info("step answer was not the agreed JSON; asking once for a rewrite")
    previous = f"\n\nYour previous answer:\n{prompts.fence(answer)}" if answer else ""
    messages = (
        {"role": "system", "content": system},
        {"role": "user", "content": f"Tool results from this step:\n{prompts.fence(seen)}{previous}\n\n"
                                    f"{prompts.REPAIR_INSTRUCTION}"},
    )
    try:
        return parse_object(await ask_once(deps, messages, reader.budget))
    except BudgetExceeded:
        log.info("no LLM budget left for a repair")
        return None
