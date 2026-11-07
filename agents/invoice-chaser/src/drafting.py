"""The draft step: one tool-less LLM call per requested invoice (at most five a turn), answered from the
stored list. Level, deadline and history come from code (ladder.py) as data. Recipient, subject and
thread come from the checked invoice and its verified reminders, never from the model. Level 4 is an
internal note rendered in code, never an email. The send tool must be one the gateway lists or its tool
search finds this request, and whose name reads as a write; otherwise the reminder is offered to copy."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import ladder
import prompts
import render
from checks import EMAIL
from deps import TurnDeps
from discovery import discover
from drafts import Draft, body_problem, parse_draft, replace_links
from invoices import Entry
from nasiko_hitl import question_metadata
from nasiko_mcp import McpTool
from picker import FINAL_OUTCOMES
from steps import ask_once

log = logging.getLogger("invoice_chaser.drafting")

MAX_DRAFTS_PER_TURN = 5
ADDRESS_KEY = "invoice_chaser_address"
COPY_ONLY = "Copy only"
_NUMBER = re.compile(r"\d+")
_HASH_NUMBER = re.compile(r"#\s?(\d+)")
_DRAFT_WORDS = re.compile(r"\b(?:draft|chase|remind|reminder|nudge|follow[- ]?up|write|send)\b", re.IGNORECASE)
_ANYWAY = re.compile(r"\banyway\b|\bregardless\b|\banyhow\b", re.IGNORECASE)


@dataclass(frozen=True)
class AddressAsk:
    text: str
    hitl: dict
    metadata: dict


@dataclass(frozen=True)
class DraftTurn:
    reply: str
    drafts: tuple[Draft, ...] = ()
    ask: AddressAsk | None = None
    outcomes: Mapping[str, str] = field(default_factory=dict)
    texts: Mapping[str, str] = field(default_factory=dict)  # entry key → drafted text (for the envelope)


def requested_ids(answer: str) -> tuple[str, ...]:
    """`DRAFT:1, #3` → ("1", "3"), de-duplicated in order."""
    return tuple(dict.fromkeys(_NUMBER.findall(answer[len(prompts.DRAFT_PREFIX):])))


def ids_in_request(text: str) -> tuple[str, ...]:
    """"draft the next reminder for #2 and #4" → ("2", "4"); () unless it asks for a draft by number."""
    if not _DRAFT_WORDS.search(text or ""):
        return ()
    return tuple(dict.fromkeys(_HASH_NUMBER.findall(text)))


async def draft_turn(deps: TurnDeps, instruction: str, ids: Sequence[str], entries: Sequence[Entry],
                     view: render.View, known: Sequence[str], addresses: Mapping[str, str | None] | None = None,
                     ) -> DraftTurn:
    """`ids` are list numbers (`#n`); `addresses` maps an entry key to the address the user typed
    (None = copy only) for invoices without an email on file."""
    addresses = dict(addresses or {})
    by_id = {e.id: e for e in entries}
    notes = [f"I don't have #{i} in the list." for i in ids if i not in by_id]
    wanted: list[Entry] = []
    noted: dict[str, str] = {}
    for e in (by_id[i] for i in ids if i in by_id):
        note = _not_drafted(e, view, instruction)
        if note is None:
            wanted.append(e)
            continue
        notes.append(note)
        if view.state(e).level == 4 and not e.invoice.auto_charge:
            noted[e.key] = "noted"
    wanted = wanted[:MAX_DRAFTS_PER_TURN]
    missing = [e for e in wanted if not e.invoice.customer_email and e.key not in addresses]
    if missing:
        return DraftTurn("\n\n".join(notes), ask=_address_question(missing[0], ids, instruction, addresses), outcomes=noted)
    if not wanted:
        return DraftTurn("\n\n".join(notes) or prompts.NOTHING_TO_DRAFT, outcomes=noted)
    catalog, failure = await discover(deps, "send")
    if failure == prompts.NO_TRACE_CONTEXT:
        return DraftTurn(failure)
    senders = catalog.send_tools() if catalog else ()
    drafts: tuple[Draft, ...] = ()
    texts: dict[str, str] = {}
    known_set = {k.casefold() for k in known}
    for e in wanted:
        to = e.invoice.customer_email or addresses.get(e.key)
        sendable = bool(e.invoice.customer_email) or bool(to and to.casefold() in known_set)
        draft, note, text = await _draft_one(deps, instruction, e, senders, view, to, sendable)
        drafts = (*drafts, draft) if draft else drafts
        notes = [*notes, note] if note else notes
        texts = {**texts, e.key: text} if text else texts
    return DraftTurn("\n\n".join(notes), drafts, outcomes=noted, texts=texts)


def _not_drafted(e: Entry, view: render.View, instruction: str) -> str | None:
    """Why this invoice gets no customer email now, or None when it can be drafted."""
    s = view.state(e)
    outcome = view.outcomes.get(e.key)
    if outcome in FINAL_OUTCOMES:
        return f"#{e.id} was already handled ({outcome}), so I won't draft it again."
    if e.invoice.auto_charge:
        return f"#{e.id} {e.invoice.label} is an auto-charge invoice ({ladder.AUTO_CHARGE_ACTION}); I don't draft for it."
    if s.level == 4:
        return render.internal_note(e, view)
    if s.cooling_down and not _ANYWAY.search(instruction or ""):
        return (f"#{e.id} {e.invoice.label}: a reminder went out less than {ladder.COOLDOWN_DAYS} days ago, so I'd wait "
                f"{s.cooldown_left} more day(s). Say \"draft for #{e.id} anyway\" if you want one now.")
    return None


def _address_question(e: Entry, ids: Sequence[str], instruction: str,
                      answered: Mapping[str, str | None]) -> AddressAsk:
    header = f"Which address should I use for {e.invoice.label}?"
    header = header if len(header.encode("utf-8")) <= 200 else "Which address should I use?"
    hitl = question_metadata(header, [(COPY_ONLY, "Draft it for me to copy; nothing is sent.")],
                             multi_select=False, allow_custom_input=True)
    text = (f"#{e.id} {e.invoice.label} ({e.invoice.customer}) has no email address on file. Which address should "
            f"I use? Type it, or pick {COPY_ONLY}. I only send to an address I saw in your invoices or email.")
    return AddressAsk(text, hitl, {ADDRESS_KEY: {"key": e.key, "ids": list(ids), "instruction": instruction,
                                                 "answered": dict(answered)}})


def typed_address(answer: str) -> str | None:
    match = EMAIL.search(answer or "")
    return match.group(0) if match else None


async def _draft_one(deps: TurnDeps, instruction: str, e: Entry, senders: Sequence[McpTool], view: render.View,
                     to: str | None, sendable: bool) -> tuple[Draft | None, str | None, str | None]:
    s = view.state(e)
    inv = e.invoice
    tool_list = json.dumps([{"name": t.name, "description": t.description} for t in senders], ensure_ascii=False)
    reply = f"\nThe customer replied; acknowledge it. Their reply subject: \"{e.reply.subject}\"" if e.reply else ""
    record = (f"{render.render_for_model((e,), view)}\nLevel: {s.level} {s.name}{reply}\n"
              f"Write the amount exactly as: {ladder.money(inv.amount_due_minor, inv.currency)}")
    system = (f"{prompts.draft_system(s.level, deps.today, ladder.response_deadline(deps.today))}\n\n"
              f"{prompts.fence_prior(record)}\n\n{prompts.fence_candidates(tool_list)}")
    request = instruction or f"Draft the next reminder for #{e.id}."
    text = parse_draft(await ask_once(deps, ({"role": "system", "content": system},
                                             {"role": "user", "content": request})))
    if text is None:
        return None, f"I couldn't draft #{e.id}; ask me again.", None
    body = replace_links(text.body, inv.hosted_url)
    problem = body_problem(body, to or "", ladder.money(inv.amount_due_minor, inv.currency))
    if problem is not None:
        log.info("draft for #%s refused: %s", e.id, problem)
        return None, f"I couldn't draft #{e.id} safely ({problem}); ask me again.", None
    subject, thread = _subject_and_thread(e)
    who = inv.customer_name or to or "[customer email]"
    if not to or not sendable:
        reason = ("I couldn't find that address in your invoices or email, so I won't send to it." if to
                  else "There is no email address on file, so I can't send it.")
        return None, render.render_copyable(e.id, f"{who} <{to or '[customer email]'}>", subject, body, reason), body
    if text.tool not in {t.name for t in senders}:
        log.info("draft for #%s names no listed send tool", e.id)
        reason = "I have no granted email send tool, so I can't send it."
        return None, render.render_copyable(e.id, f"{who} <{to}>", subject, body, reason), body
    draft = Draft(entry_id=e.id, key=e.key, number=inv.label, to=to, to_name=inv.customer_name or "",
                  subject=subject, body=body, tool=text.tool, thread_ref=thread, level_name=s.label)
    return draft, None, body


def _subject_and_thread(e: Entry) -> tuple[str, str]:
    """Reply in the last reminder's thread when one was read, else a new email."""
    last = next((r for r in reversed(e.reminders) if r.subject and r.thread_ref), None)
    if last is None:
        return f"Invoice {e.invoice.label} past due", ""
    subject = last.subject if last.subject.casefold().startswith("re:") else f"Re: {last.subject}"
    return subject, last.thread_ref
