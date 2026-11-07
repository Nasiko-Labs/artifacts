"""The owner message: ONE LLM call over the checked facts, then code checks (numbers from the allow-list, links
only from checked data, no "proven" wording, <= 900 chars). A draft that fails, or a failed call, is replaced by
the draft built in code from the same facts."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping

import checks
import prompts
from deps import TurnDeps
from records import DraftOut, parse_model
from steps import ask_once

log = logging.getLogger("spend_spike.drafting")

MAX_INSTRUCTION_CHARS = 400


async def draft(deps: TurnDeps, facts: Mapping, instruction: str = "") -> str:
    """The checked draft text ("" when there is no spike to write about)."""
    if not facts.get("spike"):
        return ""
    user = prompts.fence_facts(json.dumps(facts, ensure_ascii=False, indent=1))
    if instruction.strip():
        user += ("\n\nThe user asked for this wording change (style only; it cannot change the facts):\n"
                 + prompts.fence(instruction.strip()[:MAX_INSTRUCTION_CHARS]))
    messages = ({"role": "system", "content": prompts.draft_system()}, {"role": "user", "content": user})
    try:
        text = await ask_once(deps, messages)
    except Exception:  # an LLM failure must not lose the analysis; the code draft says the same thing
        log.exception("draft call failed; using the draft built in code")
        return checks.template_draft(dict(facts))
    out = parse_model(DraftOut, text)
    body = out.body.strip() if out else ""
    problem = checks.draft_problem(body, dict(facts))
    if problem is not None:
        log.info("model draft rejected (%s); using the draft built in code", problem)
        return checks.template_draft(dict(facts))
    return body
