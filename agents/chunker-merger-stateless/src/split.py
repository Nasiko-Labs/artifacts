"""Pure: one split, end to end. Resolve the size and strategy, cut, verify losslessly (falling back to a
plain paragraph split with a note if the check fails), then build the chunk prompts and the envelope."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields, replace

import splitters
import verify
from prompts_out import MERGE_INSTRUCTION, chunk_prompt, strip_markers
from splitters import STRATEGIES, Span

DEFAULT_SIZE = 20_000
MIN_SIZE = 2_000
MAX_SIZE = 60_000
CHARS_PER_TOKEN = 4
GROWTH_STEP = 10  # widen the size by 1/10 while boundary packing overshoots the part cap


@dataclass(frozen=True)
class SplitOptions:
    size: int | None = None
    parts: int | None = None
    strategy: str | None = None      # None: auto
    overlap_units: int = 0
    overlap_chars: int = 0
    step: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict | None) -> SplitOptions:
        data = data or {}
        known = {f.name for f in fields(SplitOptions)}
        clean = {k: v for k, v in data.items() if k in known}
        for key in ("size", "parts", "overlap_units", "overlap_chars"):
            if clean.get(key) is not None:
                clean[key] = int(clean[key])  # protobuf Struct numbers come back as floats
        return SplitOptions(**clean)

    def overlay(self, newer: SplitOptions) -> SplitOptions:
        """These options with every field the newer request sets (a size replaces a part count)."""
        changes = {f.name: getattr(newer, f.name) for f in fields(SplitOptions) if getattr(newer, f.name)}
        if newer.size:
            changes["parts"] = None
        if newer.parts:
            changes["size"] = None
        return replace(self, **changes)


@dataclass(frozen=True)
class SplitResult:
    parts: tuple[dict, ...]
    strategy: str
    chars: int
    confidence: str
    notes: tuple[str, ...]

    @property
    def summary(self) -> str:
        n = len(self.parts)
        if n == 1:
            return f"{self.chars:,} chars fit in one part (by {self.strategy})."
        return f"Split {self.chars:,} chars into {n} parts (by {self.strategy}, ~{round(self.chars / n):,} chars each)."

    def envelope(self) -> dict:
        items = [dict(p) for p in self.parts]
        return {"summary": self.summary, "items": items, "chunks": [dict(p) for p in items],
                "merge_instruction": MERGE_INSTRUCTION, "strategy": self.strategy, "notes": list(self.notes),
                "confidence": self.confidence, "needs_human": False}


def split_text(text: str, opts: SplitOptions) -> SplitResult:
    strategy, notes = choose_strategy(text, opts.strategy)
    starts = splitters.unit_starts(text, strategy)
    spans, size_notes = _spans(text, starts, opts)
    notes += size_notes
    bad = verify.problem(text, spans)
    if bad is not None:
        notes.append(f"The {strategy} split failed its check ({bad}); fell back to a plain paragraph split.")
        strategy = "paragraphs"
        starts = splitters.unit_starts(text, strategy)
        spans = _fallback_spans(text, starts)
        if verify.problem(text, spans) is not None:
            raise RuntimeError("the fallback split failed its lossless check")
    parts, stripped = _parts(text, spans, starts, strategy, opts.step)
    if stripped:
        notes.append(f"Removed {stripped} fence marker(s) from the text so each part's fence stays intact.")
    mid = any(s.mid for s in spans)
    if mid:
        notes.append("Some units were longer than a part, so they were cut at a sentence or line break "
                     "(flagged split_mid_unit).")
    confidence = "high" if strategy in ("turns", "headings") and not mid and bad is None else "medium"
    return SplitResult(tuple(parts), strategy, len(text), confidence, tuple(notes))


def choose_strategy(text: str, requested: str | None) -> tuple[str, list[str]]:
    detected = splitters.detect(text)
    if requested not in STRATEGIES:
        return detected, []
    if requested == "sentences" or len(splitters.FINDERS[requested](text)) >= 2:
        return requested, []
    return detected, [f"I found no {splitters.UNIT_WORDS[requested]} to split by, so I split by {detected}."]


def resolve_size(length: int, opts: SplitOptions) -> tuple[str, int, list[str]]:
    """("parts", n) or ("size", chars), with notes on any clamp."""
    notes: list[str] = []
    if opts.parts:
        n = min(max(1, opts.parts), verify.MAX_PARTS)
        if n != opts.parts:
            notes.append(f"I split into at most {verify.MAX_PARTS} parts.")
        if length / n > MAX_SIZE:
            n = math.ceil(length / MAX_SIZE)
            notes.append(f"{opts.parts} parts would be over {MAX_SIZE:,} chars each, so I made {n}.")
        elif n > 1 and length / n < MIN_SIZE:
            n = max(1, length // MIN_SIZE)
            notes.append(f"Parts under {MIN_SIZE:,} chars aren't useful, so I made {n}.")
        return "parts", n, notes
    size = opts.size or DEFAULT_SIZE
    clamped = min(max(size, MIN_SIZE), MAX_SIZE)
    if clamped != size:
        notes.append(f"Part size is kept between {MIN_SIZE:,} and {MAX_SIZE:,} chars, so I used {clamped:,}.")
    if math.ceil(length / clamped) > verify.MAX_PARTS:
        clamped = math.ceil(length / verify.MAX_PARTS)
        notes.append(f"That would be over {verify.MAX_PARTS} parts, so each part is up to {clamped:,} chars.")
    return "size", clamped, notes


def _spans(text: str, starts: list[int], opts: SplitOptions) -> tuple[list[Span], list[str]]:
    mode, value, notes = resolve_size(len(text), opts)
    if mode == "parts":
        cuts, mids = splitters.pack_into(text, starts, value)
    else:
        cuts, mids = splitters.pack_by_size(text, starts, value)
        while len(cuts) + 1 > verify.MAX_PARTS:
            value += value // GROWTH_STEP
            cuts, mids = splitters.pack_by_size(text, starts, value)
    spans = splitters.to_spans(len(text), cuts, mids)
    return splitters.with_overlap(text, spans, starts, opts.overlap_units, opts.overlap_chars), notes


def _fallback_spans(text: str, starts: list[int]) -> list[Span]:
    size = max(DEFAULT_SIZE, math.ceil(len(text) / verify.MAX_PARTS))
    cuts, mids = splitters.pack_by_size(text, starts, size)
    return splitters.to_spans(len(text), cuts, mids)


def _parts(text: str, spans: list[Span], starts: list[int], strategy: str, step: str) -> tuple[list[dict], int]:
    parts, stripped, total = [], 0, len(spans)
    for n, span in enumerate(spans, 1):
        body, removed = strip_markers(text[span.view_start:span.end].strip())
        stripped += removed
        label = splitters.unit_label(starts, strategy, span)
        overlap = len(text[span.view_start:span.start].lstrip())
        parts.append({"n": n, "of": total, "chars": len(body), "est_tokens": math.ceil(len(body) / CHARS_PER_TOKEN),
                      "label": label, "split_mid_unit": span.mid, "overlap_chars": overlap,
                      "prompt": chunk_prompt(n, total, label, step, body, overlap)})
    return parts, stripped
