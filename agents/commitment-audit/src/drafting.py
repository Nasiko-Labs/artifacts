"""The rollup: one tool-less call words it from the derived rows (fenced), then code makes it true
(rollup.finalize). A re-draft follows the user's instruction the same way."""

from __future__ import annotations

import logging
from collections.abc import Sequence

import openai

import prompts
import rollup
from deps import TurnDeps
from derive import Row
from quarter import Quarter
from reader import ask_once

log = logging.getLogger("commitment_audit.drafting")


def title(quarter: Quarter, today) -> str:
    """The document title, set in code and bound into the approval."""
    return f"Commitment audit, {quarter.label}, {today.isoformat()}"


async def draft(deps: TurnDeps, rows: Sequence[Row], not_audited: Sequence[str], quarter: Quarter) -> str:
    """Always a rollup: if the wording call fails, code writes every section itself."""
    data = prompts.fence_audit(rollup.model_data(rows, not_audited))
    system = f"{prompts.rollup_system(quarter, deps.today)}\n\n{data}"
    try:
        text = await ask_once(deps, ({"role": "system", "content": system},
                                     {"role": "user", "content": "Write the rollup."}))
    except openai.OpenAIError as e:
        log.warning("rollup wording call failed (%s); code writes the rollup", type(e).__name__)
        text = ""
    return rollup.finalize(text, rows, not_audited, title(quarter, deps.today))


async def redraft(deps: TurnDeps, rows: Sequence[Row], not_audited: Sequence[str], quarter: Quarter,
                  current: str, instruction: str) -> str | None:
    """The rewritten rollup, or None when the instruction was off topic."""
    data = prompts.fence_audit(rollup.model_data(rows, not_audited))
    system = (f"{prompts.edit_rollup_system(quarter, deps.today)}\n\n{data}\n\n"
              f"{prompts.fence_rollup(_tokenised(current, rows))}")
    text = await ask_once(deps, ({"role": "system", "content": system}, {"role": "user", "content": instruction}))
    if text.strip().startswith(prompts.OFF_TOPIC):
        return None
    return rollup.finalize(text, rows, not_audited, title(quarter, deps.today))


def _tokenised(text: str, rows: Sequence[Row]) -> str:
    """The stored rollup with code's status text turned back into tokens (per line, for the one commitment
    the line names), so the model keeps them."""
    return "\n".join(_tokenised_line(line, rows) for line in text.splitlines())


def _tokenised_line(line: str, rows: Sequence[Row]) -> str:
    named = [r for r in rows if r.name in line and rollup.status_text(r) in line]
    return line.replace(rollup.status_text(named[0]), rollup.token(named[0].cid)) if len(named) == 1 else line
