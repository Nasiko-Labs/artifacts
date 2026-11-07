"""One prep run: extract (model) -> check (code) -> assess (model) -> questions (model, checked) -> rank and
assemble (code). On a failed or late assess call, the deadline fallback renders the carry-overs unassessed and
the accomplishments, with standing questions only."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import assess
import dates
import envelope
import extract
import questions as questions_mod
import rank
from check import verify
from llm import LlmError, TurnDeps
from split import Paste

log = logging.getLogger("one-on-one-prep.pipeline")


@dataclass(frozen=True)
class Outcome:
    status: str                 # ok | failed | off_topic
    envelope: dict = field(default_factory=dict)


def _notes(paste: Paste, raw: extract.Raw, fallback: bool) -> list[str]:
    notes = []
    if paste.mixed:
        notes.append("No Notes:/Updates: labels found, so notes and updates were read together (marked pasted).")
    if not paste.updates and not paste.mixed:
        notes.append("No updates were pasted, so nothing could be checked as done.")
    if raw.skipped_chars:
        notes.append(f"About {raw.skipped_chars:,} characters were not read (too long); paste less to cover it all.")
    if fallback:
        notes.append("Ran out of time: last time's commitments were not checked against the updates.")
    return notes


def _header(paste: Paste, today, notes: list[str], sensitive_included: bool) -> dict:
    anchor = dates.parse(paste.last_date, today) if paste.last_date else None
    return {"person": paste.person, "role": paste.role,
            "last_1on1": dates.short(anchor) if anchor else paste.last_date, "next_1on1": paste.next_date,
            "as_of": today.isoformat(), "notes": notes, "sensitive_included": sensitive_included}


async def run(deps: TurnDeps, paste: Paste, policy: Sequence[str] = (), personal: int = 0,
              sensitive_included: bool = False) -> Outcome:
    updates_text = paste.notes if paste.mixed else paste.updates
    try:
        raw = await extract.run(deps, paste.notes, paste.updates, paste.mixed, policy)
    except LlmError as e:
        log.warning("extract failed: %s", e)
        return Outcome("failed")
    if raw.off_topic:
        return Outcome("off_topic")
    verified = verify(raw, paste.notes, updates_text, paste.person)
    found, unverified = verified.items, verified.unverified
    fallback = False
    try:
        commitments = await assess.run(deps, found["commitments"], updates_text, policy)
    except LlmError as e:
        log.warning("assess failed, deadline fallback: %s", e)
        commitments, fallback = assess.unassessed(found["commitments"]), True
    today = deps.today
    anchor = dates.parse(paste.last_date, today) if paste.last_date else None
    update_items = found["accomplishments"] + found["events"]
    points = rank.talking_points(paste.topics, commitments, found["blockers"], found["concerns_raised"],
                                 found["goals"], update_items, paste.person, today, anchor)
    recognized = rank.recognize(found["accomplishments"], today, anchor)
    asked: list[dict] = []
    if not fallback:
        managers = {c["id"] for c in commitments if c["who"] == "manager"}  # the manager's to answer, not ask
        about = [p for p in points if p["source"] != "manager" and p["id"] not in managers]
        ground = "\n".join([paste.notes, paste.updates, paste.person, *paste.topics])
        asked = await questions_mod.propose(deps, about, ground, policy)
    asked = questions_mod.fill(asked, no_recognition=not recognized)
    total = sum(len(v) for v in found.values())
    facts = envelope.Facts(has_notes=bool(paste.notes.strip()), has_updates=bool(paste.updates.strip()),
                           mixed=paste.mixed, verified=len(commitments) + len(found["accomplishments"]),
                           total_verified=total, fallback=fallback, sensitive_included=sensitive_included)
    carry = [{"id": c["id"], "text": c["text"], "who": c["who"], "status": c["status"], "quote": c["quote"],
              **({"evidence": c["evidence"]} if c.get("evidence") else {}),
              **({"due": c["due"]} if c.get("due") else {})} for c in commitments]
    header = _header(paste, today, _notes(paste, raw, fallback), sensitive_included)
    left_out = {"personal": personal, "unverified": unverified}
    return Outcome("ok", envelope.build(points, asked, recognized, carry, facts, left_out, header))
