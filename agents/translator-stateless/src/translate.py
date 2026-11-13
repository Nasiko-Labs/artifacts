"""One translation: mask → split → translate chunks (≤ 4 at once) → verify → one repair per failing chunk →
code fixes → restore. Each chunk call gets the previous chunk's last segment as read-only context."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field

import languages
import llm
import mask
import prompts
import register as reg
import segments as segs
import verify
from request import ENVELOPE_FIELDS, Job

CONCURRENCY = 4


@dataclass
class Prepared:
    """The masked job: segments in order, the token → original map, and the docs they came from."""

    segments: list[segs.Segment]
    spans: dict[str, str]
    docs: list[tuple[str | None, str]]


@dataclass
class ChunkResult:
    status: str                         # ok | failed | skipped
    translations: list[str] = field(default_factory=list)
    source_language: str = ""
    notes: list = field(default_factory=list)       # raw model notes
    code_notes: list[str] = field(default_factory=list)
    repaired: bool = False
    digits_left: bool = False
    structure_fixed: bool = False


def prepare(job: Job) -> Prepared:
    if job.envelope:
        docs = [(f, job.envelope[f]) for f in ENVELOPE_FIELDS
                if isinstance(job.envelope.get(f), str) and job.envelope[f].strip()]
    else:
        docs = [(None, job.source)]
    segments: list[segs.Segment] = []
    spans: dict[str, str] = {}
    for name, text in docs:
        masked, found = mask.mask(text, job.keep, first=len(spans) + 1)
        spans.update(found)
        segments += segs.split(masked, name)
    return Prepared(segments, spans, docs)


async def run(deps: llm.TurnDeps, job: Job, prepared: Prepared, context: Sequence[str] = ()) -> list[ChunkResult]:
    chunks = segs.chunk(prepared.segments)
    gate = asyncio.Semaphore(CONCURRENCY)

    async def one(index: int, ids: list[int]) -> ChunkResult:
        previous = prepared.segments[chunks[index - 1][-1]].text if index else ""
        async with gate:
            return await _chunk(deps, job, [prepared.segments[i].text for i in ids], ids[0] + 1, previous,
                                prepared.spans, context)

    return list(await asyncio.gather(*(one(i, ids) for i, ids in enumerate(chunks))))


async def _chunk(deps: llm.TurnDeps, job: Job, sources: list[str], first: int, previous: str,
                 spans: dict[str, str], context: Sequence[str]) -> ChunkResult:
    if not deps.has_time():
        return ChunkResult("skipped")
    target = languages.prompt_name(job.target)
    rule = reg.rule(job.target.split("-")[0].casefold(), job.register)
    glossary = [f"keep '{k}' untranslated" for k in job.keep] + [f"translate '{a}' as '{b}'" for a, b in job.glossary]
    messages = prompts.translate_messages(target, rule, sources, previous, glossary, job.source_hint, context)
    try:
        raw = await llm.call(deps, messages)
    except llm.LlmError:
        return ChunkResult("failed")
    parsed = _parse(raw, len(sources))
    problems = _problems(parsed, sources, first, job)
    if not problems:
        return _finish(parsed, sources, first, spans, repaired=False)
    repaired = None
    if deps.has_time():
        try:
            repaired = _parse(await llm.call(deps, prompts.repair_messages(messages, raw, problems)), len(sources))
        except llm.LlmError:
            repaired = None
    best = repaired or parsed
    if best is None:
        return ChunkResult("failed")
    return _finish(best, sources, first, spans, repaired=repaired is not None)


def _parse(raw: str, n: int) -> dict | None:
    data = llm.load(raw)
    if not isinstance(data, dict):
        return None
    out = data.get("segments")
    if not isinstance(out, list) or len(out) != n or not all(isinstance(s, str) for s in out):
        return None
    return {"segments": [mask.normalize_tokens(s) for s in out], "notes": data.get("notes"),
            "source_language": str(data.get("source_language") or "").strip()}


def _problems(parsed: dict | None, sources: list[str], first: int, job: Job) -> list[str]:
    if parsed is None:
        return [f"the answer must be one JSON object whose \"segments\" list has exactly {len(sources)} strings"]
    found = verify.chunk_problems(sources, parsed["segments"], first)
    same_language = parsed["source_language"].split("-")[0].casefold() == job.target.split("-")[0].casefold()
    if not same_language and verify.unchanged(sources, parsed["segments"]):
        found.append(f"the segments were returned untranslated; translate them into {job.target}")
    return found


def _finish(parsed: dict, sources: list[str], first: int, spans: dict[str, str], repaired: bool) -> ChunkResult:
    texts: list[str] = []
    code_notes: list[str] = []
    fixed_structure = False
    for offset, (src, out) in enumerate(zip(sources, parsed["segments"], strict=True)):
        placed, notes = verify.place_tokens(src, out, spans, first + offset)
        if segs.shape(src) != segs.shape(placed):
            placed, changed = segs.restore_markers(src, placed)
            fixed_structure = fixed_structure or changed or segs.shape(src) != segs.shape(placed)
        texts.append(placed)
        code_notes += notes
    digits_left = verify.digit_problem("\n".join(sources), "\n".join(texts)) is not None
    if digits_left:
        code_notes.append(f"Numbers differ from the source in segments {first}-{first + len(sources) - 1}; check them.")
    if fixed_structure:
        code_notes.append(f"List or heading markers were restored in segments {first}-{first + len(sources) - 1}.")
    return ChunkResult("ok", texts, parsed["source_language"], parsed["notes"] or [], code_notes, repaired,
                       digits_left, fixed_structure)
