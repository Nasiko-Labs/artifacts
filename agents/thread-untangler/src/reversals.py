"""Reversals: one extra call over the checked decisions and the messages after them, then code verifies
every reversal (from that call or from the extract calls) and applies it.

Verified when: the decision and the later message exist, the later message comes after the decision's
message, its quote is in that later message (or, re-pointed, in exactly one later message), and the
later message shares a content word with the decision or carries an override cue."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

import checks
import grounding as g
import llm
import prompts
from model import Action, Decision, Reversal
from thread import Thread

log = logging.getLogger("thread_untangler.reversals")

FOLLOWING_MAX_CHARS = 300
LATER_BLOCK_MAX_CHARS = 24_000
# The plan's cue list, plus the "moved to / moving to / pushed to" family its own Monday -> Wednesday
# example needs ("Legal says no, moving to Wednesday" shares no content word with "launch Monday").
OVERRIDE_CUES = ("actually", "scratch that", "scrap that", "instead", "no longer", "changed my mind", "update:",
                 "on second thought", "let's not", "lets not", "revert", "never mind", "nevermind", "cancel",
                 "moving to", "moved to", "move it to", "pushed to", "push it to", "postpone", "not going ahead")


def wanted(decisions: Sequence[Decision], chunks: int) -> bool:
    return len(decisions) >= 2 or (chunks > 1 and len(decisions) >= 1)


async def find(deps: llm.TurnDeps, decisions: Sequence[Decision], thread: Thread,
               context: Sequence[str] = ()) -> list[dict]:
    """One call, no repair (the turn's budget). Decisions are shown as D1..Dn; ids are mapped back here."""
    labels = {f"D{n}": d for n, d in enumerate(decisions, start=1)}
    lines = [f'{label} [{d.message_id}] {d.speaker}: "{d.quote}"' for label, d in labels.items()]
    try:
        parsed = await llm.complete_json(deps, prompts.reversal_messages("\n".join(lines),
                                                                         later_block(decisions, thread), context),
                                         prompts.REVERSAL_CONTRACT, parse)
    except llm.LlmError:
        log.warning("reversal pass failed; only reversals from the extract calls are checked")
        return []
    found = []
    for entry in parsed["reversals"]:
        decision = labels.get(str(entry.get("earlier_decision_id") or "").strip().upper())
        if decision is not None:
            found.append({**entry, "earlier_decision_id": decision.key})
    return found


def later_block(decisions: Sequence[Decision], thread: Thread) -> str:
    first = min(thread.index(d.message_id) or 0 for d in decisions)
    lines, used = [], 0
    for message in thread.messages[first + 1:]:
        if message.noise:
            continue
        text = " ".join(message.text.split())
        entry = f"[{message.id}] {message.speaker}: {text[:FOLLOWING_MAX_CHARS]}"
        if used + len(entry) > LATER_BLOCK_MAX_CHARS:
            break
        lines.append(entry)
        used += len(entry) + 1
    return "\n".join(lines)


def parse(data: Any) -> dict | None:
    if not isinstance(data, Mapping) or not isinstance(data.get("reversals"), list):
        return None
    if not all(isinstance(e, Mapping) for e in data["reversals"]):
        return None
    return {"reversals": [dict(e) for e in data["reversals"]]}


def verify(raw: Sequence[Mapping[str, Any]], decisions: Sequence[Decision],
           thread: Thread) -> tuple[tuple[Reversal, ...], int]:
    """(verified reversals, how many were dropped). One reversal per (decision, later message)."""
    by_key = {d.key: d for d in decisions}
    full = "\n".join(m.text for m in thread.messages)
    norm = g.normalise(full)
    kept: dict[tuple[str, str], Reversal] = {}
    dropped = 0
    for entry in raw:
        reversal = _one(entry, by_key, thread, full, norm)
        if reversal is None:
            dropped += 1
        else:
            kept.setdefault((reversal.decision_key, reversal.later_message_id), reversal)
    return tuple(kept.values()), dropped


def _one(entry: Mapping[str, Any], by_key: Mapping[str, Decision], thread: Thread, full: str,
         norm: str) -> Reversal | None:
    decision = by_key.get(str(entry.get("earlier_decision_id") or ""))
    quote = " ".join(str(entry.get("quote") or "").split())
    if decision is None or not quote:
        return None
    later_id = checks.anchor(quote, str(entry.get("later_message_id") or ""), thread)
    earlier_at, later_at = thread.index(decision.message_id), thread.index(later_id or "")
    if later_at is None or earlier_at is None or later_at <= earlier_at:
        return None
    later = thread.messages[later_at]
    if not _overrides(later.text, decision):
        return None
    what = checks.clean_text(entry.get("what_changed"), full)
    if what and g.ungrounded_tokens(what, norm):
        return None
    return Reversal(decision_key=decision.key, decision_text=decision.text, decision_message=decision.message_id,
                    later_message_id=later.id, speaker=later.speaker,
                    what_changed=checks.cut_words(what, checks.WORD_CAPS["reversal"]), quote=checks.cap_quote(quote))


def _overrides(later_text: str, decision: Decision) -> bool:
    plain = g.normalise(later_text)
    if any(cue in plain for cue in OVERRIDE_CUES):
        return True
    return bool(g.content_words(later_text) & (g.content_words(decision.quote) | g.content_words(decision.text)))


def apply(decisions: Sequence[Decision], actions: Sequence[Action],
          reversals: Sequence[Reversal]) -> tuple[tuple[Decision, ...], tuple[Action, ...]]:
    """Reversed decisions are marked (the earliest reversal wins); their action items are superseded."""
    first: dict[str, Reversal] = {}
    for r in reversals:
        first.setdefault(r.decision_key, r)
    marked = tuple(replace(d, status="reversed", reversed_in=first[d.key].later_message_id) if d.key in first else d
                   for d in decisions)
    reversed_ = [d for d in marked if d.status == "reversed"]
    updated = tuple(replace(a, status="superseded") if any(_same_matter(a, d) for d in reversed_) else a
                    for a in actions)
    return marked, updated


def _same_matter(action: Action, decision: Decision) -> bool:
    if action.message_id == decision.message_id:
        return True
    a, d = g.normalise(action.quote), g.normalise(decision.quote)
    return bool(a and d) and (a in d or d in a)
