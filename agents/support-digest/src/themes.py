"""Themes: the model labels each ticket's subject (one tool-less call per 25 tickets, concurrently), the
labels are validated in code, clustered (cluster.py), then counted, shared, filtered (>= 3) and ranked in
code. The model never counts or ranks."""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import prompts
from budget import TurnLimitReached
from deps import TurnDeps
from reader import ask_once
from records import Theme, Ticket, parse_labels, parse_time

_SPACES = re.compile(r"\s+")
_EDGE = re.compile(r"^[\W_]+|[\W_]+$")
_MIN_WORDS, _MAX_WORDS, _MAX_LABEL_CHARS = 2, 6, 60
_SUBJECT_CHARS = 200


@dataclass(frozen=True)
class Labelled:
    labels: Mapping[str, str]        # ticket id → label (validated)
    chunks: int = 0
    failed_chunks: int = 0
    skipped: int = 0                 # tickets beyond the chunk cap


def clean_label(raw: str) -> str:
    """A 2-6 word label, or "" when it is not one."""
    label = _EDGE.sub("", _SPACES.sub(" ", raw or "")).strip()
    words = label.split(" ")
    if not label or len(label) > _MAX_LABEL_CHARS or not _MIN_WORDS <= len(words) <= _MAX_WORDS:
        return ""
    return label[0].upper() + label[1:]


def key(label: str) -> str:
    return label.casefold()


def _chunk_text(chunk: Sequence[Ticket]) -> str:
    lines = (f"Ticket {t.id}: {_SPACES.sub(' ', t.subject)[:_SUBJECT_CHARS]}" for t in chunk)
    return prompts.fence("\n".join(lines))


async def _label_chunk(deps: TurnDeps, chunk: Sequence[Ticket]) -> dict[str, str] | None:
    messages = ({"role": "system", "content": prompts.THEME_SYSTEM}, {"role": "user", "content": _chunk_text(chunk)})
    try:
        parsed = parse_labels(await ask_once(deps, messages))
    except TurnLimitReached:
        return None
    if parsed is None:
        return None
    ids = {t.id for t in chunk}
    cleaned = {tid: clean_label(label) for tid, label in parsed.items() if tid in ids}
    return {tid: label for tid, label in cleaned.items() if label}


async def label(deps: TurnDeps, tickets: Sequence[Ticket]) -> Labelled:
    cfg = deps.config
    with_subject = [t for t in tickets if t.subject]
    size = cfg.theme_batch
    chunks = [with_subject[i:i + size] for i in range(0, len(with_subject), size)][:cfg.max_theme_chunks]
    covered = sum(len(c) for c in chunks)
    results = await asyncio.gather(*(_label_chunk(deps, c) for c in chunks))
    labels: dict[str, str] = {}
    for found in results:
        labels.update(found or {})
    return Labelled(labels=labels, chunks=len(chunks), failed_chunks=sum(r is None for r in results),
                    skipped=len(with_subject) - covered)


def _first_created(tickets: Sequence[Ticket]) -> Ticket:
    return min(tickets, key=lambda t: (parse_time(t.created_at) is None, parse_time(t.created_at) or 0, t.id))


def count(tickets: Sequence[Ticket], labels: Mapping[str, str], theme_of: Mapping[str, str], total: int,
          min_count: int, top_n: int) -> tuple[Theme, ...]:
    """Themes with at least `min_count` tickets, top `top_n` by count (ties alphabetical). `share_pct` is of
    `total` tickets; `example` is the earliest ticket's subject, verbatim."""
    members: dict[str, list[Ticket]] = {}
    for t in tickets:
        lab = labels.get(t.id)
        if lab:
            members.setdefault(theme_of.get(key(lab), lab), []).append(t)
    sizes = Counter({name: len(group) for name, group in members.items()})
    order = sorted((n for n in sizes if sizes[n] >= min_count), key=lambda n: (-sizes[n], n.casefold()))
    return tuple(Theme(name=n, count=sizes[n], share_pct=round(sizes[n] / total * 100, 1) if total else 0.0,
                       example=_first_created(members[n]).subject) for n in order[:top_n])
