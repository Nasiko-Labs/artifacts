"""One catch-up run: discover → identity + channels → history → threads with signals → code stats and
ranking → per-channel extraction → verification → envelope. Each step reads only the chat tools; every
item is checked in code before it is shown."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import datetime

import channels as channels_
import history
import identity
import mentions
import parse
import paste
import prompts
import render
import smalltalk
from deps import TurnDeps
from discovery import Catalog, discover
from extract import Extracted, extract_all
from models import Channel, ChannelCatchup, Me, Message, Outcome, RunFacts, envelope
from steps import StepResult, StepSpec, run_step
from tool_health import unavailable_reason

log = logging.getLogger("catchup.run")

THREAD_CAP = 8
EXTRACT_CAP = 12


async def run(deps: TurnDeps, request: parse.Request, stored_since: datetime | None) -> Outcome:
    pasted = parse.pasted_lines(request.text)
    if pasted:
        return await paste.summarize(deps, request, pasted)
    catalog, failure = await discover(deps)
    if failure is not None:
        return Outcome(failure)
    start, end = parse.window(request, deps.now, stored_since)
    ident = await run_step(deps, catalog.for_step("identity"), catalog.writes,
                           StepSpec(prompts.identity_system(render.since_label(start)), request.text, 3, 3))
    if ident.status == "off_topic":
        return Outcome(smalltalk.OFF_TOPIC_REPLY)
    if ident.status == "paused":
        return Outcome(prompts.READ_NEEDS_APPROVAL)
    seen = ident.seen_text
    found = identity.channels_from(ident.answer, seen) if ident.status == "ok" else ()
    me = identity.me_from(ident.answer, seen, request.typed_handle)
    notes = _identity_notes(me, ident)
    if not found:
        return Outcome("\n\n".join([prompts.NO_CHANNELS, *(f"_Note: {n}_" for n in notes)]))
    return await _read(deps, catalog, request, me, found, (start, end), notes)


async def _read(deps: TurnDeps, catalog: Catalog, request: parse.Request, me: Me, found: Sequence[Channel],
                window: tuple[datetime, datetime], notes: list[str]) -> Outcome:
    start, end = window
    selection = channels_.select(found, request.channels)
    notes += [f"#{n}: not among the channels you are in." for n in selection.unknown_names]
    if not selection.read:
        return Outcome("\n\n".join([prompts.NO_CHANNELS, *(f"_Note: {n}_" for n in notes)]))
    if selection.not_covered:
        notes.append(f"{selection.not_covered} more channels not covered (at most {channels_.CHANNEL_CAP} per run).")
    oldest, latest = f"{start.timestamp():.0f}", f"{end.timestamp():.0f}"
    hist = await run_step(deps, catalog.for_step("history"), catalog.writes, StepSpec(
        prompts.history_system(oldest, latest), prompts.history_request([(c.id, c.label) for c in selection.read]),
        3, len(selection.read), wants_json=False, drop_unavailable=False))
    raw, errors = history.collect(hist.calls, selection.read)
    raw = await _with_threads(deps, catalog, selection.read, raw, me, window)
    return await _summarize(deps, request, me, selection, raw, errors, hist, window, notes)


async def _with_threads(deps: TurnDeps, catalog: Catalog, read: Sequence[Channel],
                        raw: Mapping[str, tuple[Message, ...]], me: Me,
                        window: tuple[datetime, datetime]) -> Mapping[str, tuple[Message, ...]]:
    wanted: list[tuple[str, str]] = []
    for c in read:
        msgs, _ = history.in_window(raw.get(c.id, ()), *window)
        wanted += [(c.id, ts) for ts in mentions.threads_to_read(msgs, me, THREAD_CAP)]
    wanted = wanted[:THREAD_CAP]
    if not wanted or deps.past_deadline():
        return raw
    step = await run_step(deps, catalog.for_step("history"), catalog.writes, StepSpec(
        prompts.threads_system(), prompts.threads_request(wanted), 2, len(wanted), wants_json=False,
        drop_unavailable=False))
    replies, _ = history.collect(step.calls, read)
    return history.merge(raw, replies)


async def _summarize(deps: TurnDeps, request: parse.Request, me: Me, selection: channels_.Selection,
                     raw: Mapping[str, tuple[Message, ...]], errors: Mapping[str, str], hist: StepResult,
                     window: tuple[datetime, datetime], notes: list[str]) -> Outcome:
    start, end = window
    windowed: dict[str, tuple[Message, ...]] = {}
    quiet = 0
    for c in selection.read:
        if c.id in errors:
            notes.append(f"{c.label}: unavailable ({unavailable_reason(errors[c.id]) or 'read failed'}).")
            continue
        if c.id not in raw:
            notes.append(f"{c.label}: not read ({'time limit' if hist.status == 'skipped' else 'no result'}).")
            continue
        msgs, total = history.in_window((m for m in raw[c.id] if not m.is_join), start, end)
        if total > len(msgs):
            notes.append(f"{c.label}: showing the latest {len(msgs)} of {total} messages.")
        if msgs:
            windowed[c.id] = msgs
        else:
            quiet += 1
    by_id = {c.id: c for c in selection.read}
    stats = [(by_id[cid], len(mentions.personal_mentions(m, me)), len(m), any(mentions.is_mine(x, me) for x in m))
             for cid, m in windowed.items()]
    ranked = channels_.rank(stats)
    extracted = {} if request.only_mentions else await extract_all(
        deps, [(c.id, c.label, windowed[c.id]) for c in ranked[:EXTRACT_CAP]])
    catchups = channels_.order(tuple(_catchup(c, windowed[c.id], me, extracted.get(c.id)) for c in ranked))
    cut = any(not c.summarized for c in catchups) or hist.status == "skipped"
    facts = RunFacts(since=start.isoformat(), until=end.isoformat(), since_label=render.since_label(start),
                     coverage=tuple(notes), channels_unavailable=len(errors), quiet=quiet,
                     not_covered=selection.not_covered, dropped_items=sum(c.dropped for c in catchups))
    env = envelope(catchups, facts)
    only = "decisions" if request.only_decisions else "mentions" if request.only_mentions else ""
    return Outcome(render.report(env, only=only), env, None if cut else end.isoformat())


def _catchup(channel: Channel, msgs: Sequence[Message], me: Me, extracted: Extracted | None) -> ChannelCatchup:
    found = mentions.personal_mentions(msgs, me)
    mention_items = tuple({"quote": mentions.quote_of(m), "author": m.author, "ts": m.ts,
                           "time": m.time_label or render.when(m.ts), "link": m.permalink, "kind": kind}
                          for m, kind in found)
    verified = extracted.verified if extracted else None
    return ChannelCatchup(
        channel=channel.label, channel_id=channel.id, message_count=len(msgs),
        thread_count=sum(1 for m in msgs if m.reply_count > 0),
        broadcasts=sum(1 for m in msgs if mentions.is_broadcast(m)),
        mentions=mention_items,
        decisions=verified.decisions if verified else (),
        highlights=verified.highlights if verified else (),
        summarized=extracted.summarized if extracted else True,
        note=extracted.note if extracted else "",
        dropped=verified.dropped if verified else 0,
        i_posted=any(mentions.is_mine(m, me) for m in msgs),
    )


def _identity_notes(me: Me, ident: StepResult) -> list[str]:
    notes = []
    if me.typed and me.known:
        notes.append(f"Your chat profile wasn't readable, so mentions are matched on \"{me.handle}\" as you typed it.")
    elif not me.known:
        notes.append("Your chat profile wasn't readable, so personal mentions can't be found. "
                     "Say \"I'm @yourhandle\" to match them.")
    reasons = sorted(set(ident.dropped.values()))
    if reasons:
        notes.append(f"Some chat read tools are unavailable for your connection ({', '.join(reasons)}).")
    return notes
