"""The reply step: a bounded tool loop that looks for a later answer from each asked person. The
model's verdicts are checked in code (checks.apply_replies); an ask it never mentions stays waiting
and is flagged "reply not checked"."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

import asks as a
import checks
import prompts
from deps import TurnDeps
from sweep import Reader

log = logging.getLogger("waiting_on.replies")

REPLIES_LOOP_CALLS = 6
TIME_BUDGET_SECS = 360  # the sweep must end by then for the reply step to run (flow limit is 600 s)


async def check_replies(deps: TurnDeps, reader: Reader, asks: Sequence[a.Ask]) -> tuple[a.Ask, ...]:
    listing = json.dumps([_view(x) for x in asks], ensure_ascii=False, indent=1)
    messages = (
        {"role": "system", "content": prompts.replies_system(deps.now)},
        {"role": "user", "content": prompts.fence_candidates(listing)},
    )
    result = await reader.loop(deps, messages, REPLIES_LOOP_CALLS)
    if result.status != "completed":
        log.info("reply check ended %s; every ask stays waiting, not checked", result.status)
        return checks.apply_replies(asks, {}, reader.fenced.seen_text)
    return checks.apply_replies(asks, a.parse_replies(result.text), reader.fenced.seen_text)


def unchecked(asks: Sequence[a.Ask]) -> tuple[a.Ask, ...]:
    """The reply step was skipped: every ask waiting, none checked (so none is offered for a nudge)."""
    return checks.apply_replies(asks, {}, "")


def _view(x: a.Ask) -> dict:
    return {"id": x.key, "source": x.source, "to": x.to, "to_name": x.to_name, "conversation": x.conversation,
            "quote": x.quote, "asked_at": x.asked_at, "thread_ref": x.thread_ref, "link": x.link}
