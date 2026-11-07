"""Pasted channel text, summarized with the same schema when no chat connection is available (or the
user pastes). Lines are `[time] name: text`; a `#channel` line starts a block. Best effort: the time is
kept as written, and confidence is always low."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import channels as channels_
import mentions
import render
from extract import extract_all
from models import Channel, ChannelCatchup, Me, Message, Outcome, RunFacts, envelope

if TYPE_CHECKING:
    from deps import TurnDeps
    from parse import Request

NOTE = "Summarized from pasted text (best effort: times as written, no links)."


async def summarize(deps: TurnDeps, request: Request, lines: Sequence[tuple[str, str, str, str]]) -> Outcome:
    me = Me(handle=request.typed_handle.lstrip("@"), typed=True) if request.typed_handle else Me(typed=True)
    grouped: dict[str, list[Message]] = {}
    for n, (channel, time, name, text) in enumerate(lines, start=1):
        grouped.setdefault(channel, []).append(
            Message(channel_id=channel, ts=str(n), user_id="", author=name, text=text, time_label=time))
    chans = {cid: Channel(id=cid, name=cid) for cid in grouped}
    extracted = await extract_all(deps, [(cid, chans[cid].label, tuple(m)) for cid, m in grouped.items()])
    catchups = []
    for cid, msgs in grouped.items():
        found = mentions.personal_mentions(msgs, me)
        verified = extracted[cid].verified
        catchups.append(ChannelCatchup(
            channel=chans[cid].label, channel_id=cid, message_count=len(msgs),
            broadcasts=sum(1 for m in msgs if mentions.is_broadcast(m)),
            mentions=tuple({"quote": mentions.quote_of(m), "author": m.author, "ts": m.ts, "time": m.time_label,
                            "link": "", "kind": kind} for m, kind in found),
            decisions=verified.decisions, highlights=verified.highlights, dropped=verified.dropped,
            summarized=extracted[cid].summarized, note=extracted[cid].note))
    notes = [NOTE]
    if not me.known:
        notes.append("Say \"I'm @yourhandle\" with the paste to find the messages that mention you.")
    facts = RunFacts(since="pasted", until="pasted", since_label="the pasted text", coverage=tuple(notes), pasted=True)
    env = envelope(channels_.order(tuple(catchups)), facts)
    return Outcome(render.report(env), env, None)
