"""Pure: the two selection pauses and reading their answers.

- pick a bug (sweep): single-select, one option per bug key;
- pick tickets: multi-select, up to 20 tickets (linked first, then tier, then oldest). The platform
  returns the selected labels one per line and any typed text as a trailing line; a line that is not a
  label is an instruction for every draft (capped). Selecting picks; it never approves text."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import tiers
from config import Config
from nasiko_hitl import (
    MAX_OPTION_DESCRIPTION_LEN,
    MAX_OPTION_LABEL_LEN,
    question_metadata,
)
from records import Bug, Ticket

PHASE_KEY = "bug_notifier"
BUG_HEADER = "Which bug should I notify customers about?"
TICKETS_HEADER = "Which tickets should I draft replies for?"
_TICKET_REF = re.compile(r"^#\s*([A-Za-z0-9_-]+)")


@dataclass(frozen=True)
class Pause:
    """An input-required pause: the text, the question block, and the task metadata to resume from."""

    text: str
    hitl: dict
    metadata: dict


def clip(text: str, max_bytes: int) -> str:
    data = (text or "").encode("utf-8")
    if len(data) <= max_bytes:
        return text or ""
    return data[:max_bytes - 3].decode("utf-8", errors="ignore").rstrip() + "..."


def bug_pause(lead: str, choices: Sequence[Bug], max_offered: int) -> Pause:
    offered = list(choices)[:max_offered]
    hitl = question_metadata(BUG_HEADER, [(b.key, clip(b.summary, MAX_OPTION_DESCRIPTION_LEN)) for b in offered],
                             multi_select=False, allow_custom_input=True)
    metadata = {PHASE_KEY: {"phase": "pick_bug", "keys": [b.key for b in offered]}}
    return Pause(f"{lead}\n\n{BUG_HEADER}", hitl, metadata)


def chosen_bug(answer: str, keys: Sequence[str]) -> str | None:
    text = (answer or "").strip().casefold()
    exact = next((k for k in keys if k.casefold() == text), None)
    return exact or next((k for k in keys if re.search(rf"(?<![a-z0-9]){re.escape(k.casefold())}(?![a-z0-9])", text)),
                         None)


def tickets_pause(lead: str, tickets: Sequence[Ticket], config: Config) -> Pause:
    ordered = sorted(tickets, key=lambda t: tiers.priority(t, config))
    offered, rest = ordered[:config.max_offered], ordered[config.max_offered:]
    options = [(label_of(t), _describe(t)) for t in offered]
    hitl = question_metadata(TICKETS_HEADER, options, multi_select=True, allow_custom_input=True)
    parts = [lead, TICKETS_HEADER + " Tick the ones to reply to; anything you type is used as an instruction for "
             "every draft (for example \"mention the workaround is no longer needed\"). Each draft is shown to you "
             "before anything is posted."]
    if rest:
        parts.append(f"Not offered (cap {config.max_offered}): " + ", ".join(f"#{t.id}" for t in rest)
                     + ". Ask again for the rest.")
    metadata = {PHASE_KEY: {"phase": "pick_tickets", "offered": {label: t.id for (label, _), t in zip(options, offered,
                                                                                                    strict=True)}}}
    return Pause("\n\n".join(parts), hitl, metadata)


def label_of(ticket: Ticket) -> str:
    return clip(f"#{ticket.id} {tiers.customer_of(ticket)}", MAX_OPTION_LABEL_LEN)


def _describe(t: Ticket) -> str:
    kind = "linked to the bug" if t.match == "linked" else "keyword match"
    return clip(f"{t.tier} tier · {t.subject or 'no subject'} · {kind}", MAX_OPTION_DESCRIPTION_LEN)


def chosen_tickets(answer: str, offered: Mapping[str, str], max_chars: int) -> tuple[tuple[str, ...], str]:
    """(ticket ids in answer order, the instruction typed alongside, capped)."""
    by_label = {label.casefold(): ticket_id for label, ticket_id in offered.items()}
    ids_offered = set(offered.values())
    ids: list[str] = []
    instruction: list[str] = []
    for line in (answer or "").splitlines():
        text = line.strip()
        if not text:
            continue
        ref = _TICKET_REF.match(text)
        ticket_id = by_label.get(text.casefold()) or (ref.group(1) if ref and ref.group(1) in ids_offered else None)
        if ticket_id is not None:
            ids.append(ticket_id)
        else:
            instruction.append(text)
    return tuple(dict.fromkeys(ids)), " ".join(instruction)[:max_chars]
