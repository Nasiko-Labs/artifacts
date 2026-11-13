"""Pure: the lossless check every split must pass before it is shown. The parts (without their
overlap) must rejoin to the input exactly, be contiguous and non-empty, and stay within the part cap."""

from __future__ import annotations

import itertools

from splitters import Span

MAX_PARTS = 40


def problem(text: str, spans: list[Span]) -> str | None:
    """None when the split is sound, else what is wrong with it."""
    if not spans:
        return "no parts"
    if len(spans) > MAX_PARTS:
        return f"{len(spans)} parts is over the cap of {MAX_PARTS}"
    if spans[0].start != 0 or spans[-1].end != len(text):
        return "the parts don't cover the whole text"
    for prev, span in itertools.pairwise(spans):
        if span.start != prev.end:
            return "the parts are not contiguous"
    for span in spans:
        if span.end <= span.start or not text[span.start:span.end].strip():
            return "a part is empty"
        if not 0 <= span.view_start <= span.start:
            return "an overlap reaches outside the text"
    if "".join(text[s.start:s.end] for s in spans) != text:
        return "the parts don't rejoin to the input"
    return None
