"""Pure: does a style pass keep the draft's facts, length, structure, and not add AI patterns? Each failure is
one plain sentence, sent back to the model in the repair call and shown to the user if it still fails."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

import grounding as g
from mask import unmask
from options import Options
from patterns import Detection, detect, word_count
from patterns_phrases import ARTEFACT_CATEGORIES, CHANGE_CATEGORIES, LABELS

LENGTH_FLOOR = 0.85
LENGTH_CEILING = 1.10
SHORTER_FLOOR = 0.60
MAX_CHANGES = 40
_BULLET = re.compile(r"^[ \t]*(?:[-*•]|\d+[.)])[ \t]+", re.MULTILINE)
_HEADING = re.compile(r"^#{1,6}[ \t]+", re.MULTILINE)
_PARAGRAPH = re.compile(r"\n[ \t]*\n")


def artefact_sentences(source: str) -> list[str]:
    return [s for s in g.sentences(source) if detect(s).has_artefact]


def kept_text(source: str) -> str:
    """The draft without its chatbot/sycophancy sentences: what must survive the pass."""
    return "\n".join(s for s in g.sentences(source) if not detect(s).has_artefact)


def check(source: str, candidate: str, options: Options, before: Detection) -> list[str]:
    if not candidate.strip():
        return ["the text is empty; return the whole edited draft"]
    return [*fact_problems(source, candidate), *length_problems(source, candidate, options, before),
            *structure_problems(source, candidate, options, before), *rescan_problems(candidate, before)]


def fact_problems(source: str, candidate: str) -> list[str]:
    kept = kept_text(source)
    problems = []
    for kind, extract in (("number", g.numbers), ("link or email", g.links)):
        added = sorted(extract(candidate) - extract(source))  # multisets: a repeat counts too
        lost = sorted(extract(kept) - extract(candidate))
        if added:
            problems.append(f"it adds {kind}s that are not in the draft: {', '.join(added)}")
        if lost:
            problems.append(f"it drops or changes {kind}s from the draft: {', '.join(lost)}")
    source_caps = g.capitalised(source)
    added_names = sorted(n for n in g.names(candidate) if n not in source_caps)
    lost_names = sorted(n for n in g.names(kept) if not g.has_word(n, candidate))
    if added_names:
        problems.append(f"it adds names that are not in the draft: {', '.join(added_names)}")
    if lost_names:
        problems.append(f"it drops names from the draft: {', '.join(lost_names)}")
    return problems


def length_problems(source: str, candidate: str, options: Options, before: Detection) -> list[str]:
    """Upper bound against the whole draft; lower bound against the draft without its artefact sentences
    and without the matched pattern phrases, which a style pass is expected to remove."""
    words_after = word_count(candidate)
    base = word_count(kept_text(source))
    removable = sum(word_count(h.phrase) for h in before.hits if h.category not in ("em_dash", *ARTEFACT_CATEGORIES))
    floor = (SHORTER_FLOOR if options.shorter else LENGTH_FLOOR) * max(1, base - removable)
    if words_after < floor:
        return [f"it is too short ({words_after} words; keep at least {round(floor)}): cut only the AI patterns"]
    if words_after > LENGTH_CEILING * word_count(source):
        return [f"it is too long ({words_after} words; the draft has {word_count(source)}): do not add content"]
    return []


def shape(text: str) -> tuple[int, int, int]:
    paragraphs = [p for p in _PARAGRAPH.split(text.strip()) if p.strip()]
    return len(paragraphs), len(_BULLET.findall(text)), len(_HEADING.findall(text))


def structure_problems(source: str, candidate: str, options: Options, before: Detection) -> list[str]:
    if "boldface_headers" in before.counts() and not options.keep_structure:
        return []  # flattening bold inline headers is the one structural change allowed
    paragraphs, bullets, headings = shape(source)
    artefact_only = sum(1 for p in _PARAGRAPH.split(source.strip()) if p.strip() and not kept_text(p).strip())
    out_paragraphs, out_bullets, out_headings = shape(candidate)
    problems = []
    if not paragraphs - artefact_only <= out_paragraphs <= paragraphs:
        problems.append(f"it has {out_paragraphs} paragraphs; keep the draft's {paragraphs - artefact_only}")
    if out_bullets != bullets:
        problems.append(f"it has {out_bullets} list items; keep the draft's {bullets}")
    if out_headings != headings:
        problems.append(f"it has {out_headings} headings; keep the draft's {headings}")
    return problems


def rescan_problems(candidate: str, before: Detection) -> list[str]:
    after, had = detect(candidate), before.counts()
    problems = []
    for category, count in after.counts().items():
        if count > had.get(category, 0):
            added = ", ".join(f'"{p}"' for p in after.phrases(category)[:4])
            problems.append(f"it adds {LABELS[category]} patterns ({added}); write plainly instead")
    return problems


def validate_changes(raw: Any, source: str, output: str, originals: tuple[str, ...]) -> tuple[list[dict], int]:
    """Kept: `before` verbatim in the draft, `after` verbatim in the output (or empty for a cut), a known
    category. Dropped entries change nothing in the text; they are only counted."""
    entries = raw if isinstance(raw, Sequence) and not isinstance(raw, str) else []
    norm_source, norm_output = _norm(source), _norm(output)
    kept, dropped = [], 0
    for entry in entries[:MAX_CHANGES]:
        change = _change(entry, originals)
        if change and change["before"] and _norm(change["before"]) in norm_source \
                and _norm(change["after"]) in norm_output:
            kept.append(change)
        else:
            dropped += 1
    return kept, dropped + max(0, len(entries) - MAX_CHANGES)


def _change(entry: Any, originals: tuple[str, ...]) -> dict | None:
    if not isinstance(entry, dict) or entry.get("category") not in CHANGE_CATEGORIES:
        return None
    before, after = entry.get("before"), entry.get("after")
    if not isinstance(before, str) or not isinstance(after or "", str):
        return None
    return {"category": entry["category"], "before": unmask(before.strip(), originals),
            "after": unmask((after or "").strip(), originals)}


def _norm(text: str) -> str:
    return " ".join((text or "").split()).casefold()
