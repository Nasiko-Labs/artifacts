"""Pure: which threads read this turn are worth judging. A thread survives only when its id came from a tool
this turn, each kept message's author and text came from a tool, it has 3+ messages from 2+ people (not only
bots), its latest message is inside the window, and no message already carries an issue key or a tracker
link. Survivors are sorted by last activity (newest first) and capped at 40, all in code."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Any

from llmcall import text_of
from models import ChatMessage, ThreadCandidate
from textcheck import appears_in, parse_day

MAX_THREADS = 40
MIN_MESSAGES = 3
MIN_AUTHORS = 2
MAX_MESSAGE_CHARS = 600
ISSUE_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
_NOT_ISSUE_KEYS = re.compile(r"^(?:UTF|ISO|SHA|MD|RFC|HTTP|TLS|SSL|CVE|COVID|GPT|IPV|AES|RSA|ES|PEP|X)-\d+$")
_URL = re.compile(r"https?://[^\s\"'<>)\]|]+")
_TRACKER_PATH = re.compile(r"/(?:issue|issues|browse|ticket|tickets)/", re.IGNORECASE)


@dataclass(frozen=True)
class Checked:
    """The threads to judge, plus how many were read and why the others were left out."""

    threads: tuple[ThreadCandidate, ...]
    read: int = 0
    unbacked: int = 0
    already_linked: int = 0


def from_answer(raw: Sequence[Any]) -> tuple[ThreadCandidate, ...]:
    """ThreadCandidates from the scan answer's `threads` (shape only; nothing is trusted yet)."""
    return tuple(_thread(t) for t in raw if isinstance(t, Mapping))


def _thread(raw: Mapping[str, Any]) -> ThreadCandidate:
    messages = raw.get("messages") if isinstance(raw.get("messages"), list) else []
    return ThreadCandidate(id=text_of(raw.get("id")), channel=text_of(raw.get("channel")).lstrip("#"),
                           channel_id=text_of(raw.get("channel_id")), thread_ts=text_of(raw.get("thread_ts")),
                           link=text_of(raw.get("link")),
                           messages=tuple(_message(m) for m in messages if isinstance(m, Mapping)))


def _message(raw: Mapping[str, Any]) -> ChatMessage:
    return ChatMessage(author=text_of(raw.get("author")), text=text_of(raw.get("text"))[:MAX_MESSAGE_CHARS],
                       ts=text_of(raw.get("ts")), bot=raw.get("bot") is True)


def check(threads: Iterable[ThreadCandidate], *, seen: str, since: date, today: date,
          tracker_hosts: Iterable[str] = ()) -> Checked:
    """Backed, big enough, in window, not already linked; newest first; renumbered t1..tn; at most 40."""
    kept: tuple[ThreadCandidate, ...] = ()
    keys: frozenset[str] = frozenset()
    read = unbacked = linked = 0
    for thread in threads:
        read += 1
        key = f"{thread.channel_id or thread.channel}/{thread.thread_ts}"
        if key in keys:
            continue
        cleaned = backed(thread, seen)
        if cleaned is None:
            unbacked += 1
            continue
        if not is_candidate(cleaned) or not in_window(cleaned, since, today):
            continue
        if has_tracker_link(cleaned, tracker_hosts):
            linked += 1
            continue
        kept = (*kept, cleaned)
        keys = keys | {key}
    ranked = sorted(kept, key=_last_activity, reverse=True)[:MAX_THREADS]
    return Checked(tuple(replace(t, id=f"t{n}") for n, t in enumerate(ranked, start=1)), read, unbacked, linked)


def backed(thread: ThreadCandidate, seen: str) -> ThreadCandidate | None:
    """The thread with only tool-backed messages (time-ordered) and a tool-backed link, or None when its
    thread id did not come from a tool."""
    if not thread.thread_ts or not appears_in(thread.thread_ts, seen):
        return None
    if thread.channel_id and not appears_in(thread.channel_id, seen):
        return None
    messages = tuple(m for m in thread.messages if appears_in(m.author, seen) and appears_in(m.text, seen))
    ordered = tuple(sorted(messages, key=lambda m: _ts_value(m.ts)))
    return replace(thread, messages=ordered, link=thread.link if appears_in(thread.link, seen) else "",
                   channel=thread.channel if appears_in(thread.channel, seen) else "")


def is_candidate(thread: ThreadCandidate) -> bool:
    """3+ messages, 2+ distinct human authors."""
    return len(thread.messages) >= MIN_MESSAGES and len(thread.participants) >= MIN_AUTHORS


def in_window(thread: ThreadCandidate, since: date, today: date) -> bool:
    last = parse_day(thread.last_ts)
    return last is not None and since <= last <= today


def issue_keys(text: str) -> tuple[str, ...]:
    return tuple(k for k in ISSUE_KEY.findall(text or "") if not _NOT_ISSUE_KEYS.match(k))


def has_tracker_link(thread: ThreadCandidate, tracker_hosts: Iterable[str] = ()) -> bool:
    """An issue key, a URL with an issue path, or a URL on a host seen in tracker output, in any message."""
    hosts = tuple(h.casefold() for h in tracker_hosts if h)
    for message in thread.messages:
        if issue_keys(message.text):
            return True
        for url in _URL.findall(message.text):
            host = url.split("/")[2].casefold() if url.count("/") >= 2 else ""
            if _TRACKER_PATH.search(url) or (host and host in hosts):
                return True
    return False


def _ts_value(ts: str) -> float:
    """Seconds since the epoch from a chat timestamp ("1762250400.000100") or an ISO datetime; 0 if unknown."""
    try:
        return float(ts)
    except ValueError:
        pass
    try:
        moment = datetime.fromisoformat(ts.strip())
    except ValueError:
        return 0.0
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp()


def _last_activity(thread: ThreadCandidate) -> float:
    return _ts_value(thread.last_ts)
