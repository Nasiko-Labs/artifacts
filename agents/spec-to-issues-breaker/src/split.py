"""The split step: the spec is chunked in code (paragraph boundaries, ~6k chars), each chunk gets one tool-less
call, and every answer is checked in code: a verbatim `source_quote` (else the issue is dropped), verbatim
acceptance lines (others dropped), no number or link the spec lacks, title cut to 80 chars. Chunks merge by
normalised title; the list is capped (the rest stated)."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import prompts
from budget import TurnLimitReached
from deps import TurnDeps
from llmcall import ask_once, load_json_object, text_of, texts_of
from proposal import renumber
from records import SIGNALS, UNCLEAR, ProposedIssue
from textcheck import appears_in, normalise, unseen_numbers_or_links

MAX_TITLE_CHARS = 80


@dataclass(frozen=True)
class SplitResult:
    issues: tuple[ProposedIssue, ...]
    extracted: int = 0            # issues the model proposed
    dropped: int = 0              # failed a verbatim check
    dropped_lines: int = 0        # acceptance lines that were not in the spec
    capped: int = 0               # issues past the cap ("ask for the rest")
    chunks: int = 0
    chunks_read: int = 0
    limit_hit: bool = False
    unreadable: int = 0           # chunks whose answer was not the agreed JSON


def chunks_of(text: str, target: int, most: int) -> tuple[str, ...]:
    """Paragraph-aligned chunks of about max(target, len/most) chars; never more than `most`."""
    size = max(target, math.ceil(len(text) / most))
    out: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        if current and len(current) + len(para) + 2 > size:
            out.append(current)
            current = ""
        while len(para) > size:
            out.append(para[:size])
            para = para[size:]
        current = f"{current}\n\n{para}" if current else para
    if current.strip():
        out.append(current)
    return tuple(out[:most])


async def split_spec(deps: TurnDeps, spec_text: str) -> SplitResult:
    parts = chunks_of(spec_text, deps.config.chunk_chars, deps.config.max_chunks)
    found: list[ProposedIssue] = []
    extracted = dropped = dropped_lines = read = unreadable = 0
    limit_hit = False
    for index, part in enumerate(parts, start=1):
        try:
            answer = await ask_once(deps, ({"role": "system", "content": prompts.SPLIT_SYSTEM},
                                           {"role": "user", "content": f"Part {index} of {len(parts)}.\n"
                                                                       + prompts.fence_spec(part)}))
        except TurnLimitReached:
            limit_hit = True
            break
        read += 1
        raw = parse_split(answer)
        if raw is None:
            unreadable += 1
            continue
        extracted += len(raw)
        kept, lost, lost_lines = check_issues(raw, spec_text)
        dropped, dropped_lines = dropped + lost, dropped_lines + lost_lines
        found.extend(_offset(kept, index * 1000))
    merged = merge(found)
    cap = deps.config.max_issues
    return SplitResult(renumber(merged[:cap]), extracted, dropped, dropped_lines, max(0, len(merged) - cap),
                       len(parts), read, limit_hit, unreadable)


def parse_split(text: str) -> tuple[ProposedIssue, ...] | None:
    data = load_json_object(text)
    if data is None or not isinstance(data.get("issues"), list):
        return None
    return tuple(i for i in (_issue(raw) for raw in data["issues"]) if i is not None)


def _issue(raw: Any) -> ProposedIssue | None:
    if not isinstance(raw, Mapping) or not text_of(raw.get("title")):
        return None
    try:
        local_id = int(raw.get("local_id"))
    except (TypeError, ValueError):
        return None
    signals = raw.get("signals") if isinstance(raw.get("signals"), Mapping) else {}
    deps: list[int] = []
    for value in raw.get("depends_on") or ():
        try:
            deps.append(int(value))
        except (TypeError, ValueError):
            continue
    return ProposedIssue(local_id=local_id, title=text_of(raw.get("title")),
                         description=text_of(raw.get("description")), source_quote=text_of(raw.get("source_quote")),
                         acceptance_criteria=texts_of(raw.get("acceptance_criteria")),
                         signals={s: signals.get(s) is True for s in (*SIGNALS, UNCLEAR)}, depends_on=tuple(deps))


def check_issues(issues: Sequence[ProposedIssue], spec: str) -> tuple[tuple[ProposedIssue, ...], int, int]:
    """Kept issues, issues dropped, acceptance lines dropped. Applied to every model-made list."""
    kept: list[ProposedIssue] = []
    lines_dropped = 0
    for issue in issues:
        if not appears_in(issue.source_quote, spec):
            continue
        if unseen_numbers_or_links(f"{issue.title} {issue.description}", spec):
            continue
        lines = tuple(dict.fromkeys(a for a in issue.acceptance_criteria if appears_in(a, spec)))
        lines_dropped += len(issue.acceptance_criteria) - len(lines)
        kept.append(replace(issue, title=issue.title[:MAX_TITLE_CHARS].strip(), acceptance_criteria=lines))
    return tuple(kept), len(issues) - len(kept), lines_dropped


def merge(issues: Sequence[ProposedIssue]) -> tuple[ProposedIssue, ...]:
    """One issue per normalised title: the first kept, later duplicates' acceptance lines and deps added."""
    by_title: dict[str, ProposedIssue] = {}
    alias: dict[int, int] = {}
    for issue in issues:
        key = normalise(issue.title).strip(" .")
        first = by_title.get(key)
        if first is None:
            by_title[key] = issue
            continue
        alias[issue.local_id] = first.local_id
        by_title[key] = replace(first, acceptance_criteria=tuple(dict.fromkeys(
            (*first.acceptance_criteria, *issue.acceptance_criteria))),
            depends_on=tuple(dict.fromkeys((*first.depends_on, *issue.depends_on))))
    return tuple(replace(i, depends_on=tuple(dict.fromkeys(alias.get(d, d) for d in i.depends_on
                                                           if alias.get(d, d) != i.local_id)))
                 for i in by_title.values())


def _offset(issues: Sequence[ProposedIssue], base: int) -> tuple[ProposedIssue, ...]:
    """Chunk-local ids made unique across chunks (dependencies stay within their chunk)."""
    return tuple(replace(i, local_id=base + i.local_id, depends_on=tuple(base + d for d in i.depends_on))
                 for i in issues)
