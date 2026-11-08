"""Pure: the text of each chunk prompt and the merge instruction. The part's text is fenced; fence
markers inside the pasted text are stripped so a part can never close its own fence."""

from __future__ import annotations

import re

DEFAULT_STEP = "Process this part of a longer text. Parts are numbered; return the same schema for each part."
MERGE_INSTRUCTION = ("When every part has run, paste each part's JSON result back to the Chunker + Merger with "
                     "'merge these' (fenced json blocks, a JSON array, or objects separated by ---); it merges "
                     "them into one result with the same schema.")
_MARKERS = re.compile(r"<<<[A-Z_]{1,40}>>>|>>>END_[A-Z_]{1,40}<<<")


def strip_markers(text: str) -> tuple[str, int]:
    """(text without fence markers, how many were removed)."""
    cleaned, count = _MARKERS.subn("", text)
    return cleaned, count


def chunk_prompt(n: int, total: int, label: str, step: str, body: str, overlap_chars: int = 0) -> str:
    links = []
    if n > 1:
        links.append(f"continues from part {n - 1}")
    if n < total:
        links.append(f"continues into part {n + 1}")
    context = "; ".join([label, *links])
    lines = [f"=== PART {n}/{total} ===", strip_markers(step)[0].strip() or DEFAULT_STEP,
             f"Part {n} of {total} ({context})."]
    if overlap_chars:
        lines.append(f"The first {overlap_chars:,} characters repeat the end of part {n - 1} for context.")
    return "\n".join([*lines, "<<<TEXT>>>", body, ">>>END_TEXT<<<"])
