"""Reported statuses: the code table first (derive.map_reported); one tool-less call for the phrases
it cannot read, skipped when time is short. Anything the model does not map cleanly is `unknown`."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

import prompts
from commitments import Commitment
from deps import TurnDeps
from derive import RANK, map_reported
from evidence import parse_items
from reader import ask_once
from verbatim import text_of

log = logging.getLogger("commitment_audit.classify")

SKIP_AFTER_SECS = 480.0


async def reported_statuses(deps: TurnDeps, items: Sequence[Commitment]) -> tuple[dict[str, str], tuple[str, ...]]:
    """cid → done / on_track / at_risk / off_track / unknown / not_reported, and any note."""
    mapped = {c.cid: ("not_reported" if not c.reported_text else map_reported(c.reported_text)) for c in items}
    unread = {c.cid: c.reported_text for c in items if mapped[c.cid] is None}
    if not unread:
        return dict(mapped), ()
    if deps.elapsed() >= SKIP_AFTER_SECS:
        log.info("skipping classify: %.0fs of the flow window already used", deps.elapsed())
        return {cid: status or "unknown" for cid, status in mapped.items()}, (prompts.CLASSIFY_TIME_NOTE,)
    phrases = json.dumps([{"cid": cid, "reported": text} for cid, text in unread.items()], ensure_ascii=False)
    answer = await ask_once(deps, ({"role": "system", "content": prompts.classify_system()},
                                   {"role": "user", "content": prompts.fence_items(phrases)}))
    by_model = {text_of(i.get("cid")): text_of(i.get("reported")) for i in parse_items(answer, "map") or []}
    return {cid: status or _valid(by_model.get(cid)) for cid, status in mapped.items()}, ()


def _valid(value: str | None) -> str:
    return value if value in RANK else "unknown"
