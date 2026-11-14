"""Pure: an explicit ask to summarize, in the user's own words, split from the text it is about.

    "summarize this: <text>", "can you summarize this for me\\n<text>", "tl;dr <text>",
    "give me the gist of this: <text>", "what are the action items here? <text>", "<text>\\n\\nsummarize"

The cue may open the message (with "please", "can you", "hey" before it and "this", "for me", "the
following" after it) or be its own last line. A cue followed by more instruction ("summarize this call,
focus on pricing: <text>") keeps that instruction. Kept in step with the other variants' copies.
"""

from __future__ import annotations

import re

INSTRUCTION_MAX_CHARS = 300
CUED_MIN_SOURCE_WORDS = 15      # an explicit cue summarizes text this long, not only >= MIN_SOURCE_WORDS
CUE_INSTRUCTION_MAX_WORDS = 20  # words after the cue that can still be part of the instruction

_CUE_LEAD = (r"(?:(?:hey|hi|ok|okay|so)[,!]?\s+)?(?:please\s+)?(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?)?"
             r"(?:just\s+)?")
_CUE_WORDS = (r"(?:summari[sz]e|sum\s+(?:it\s+|this\s+|that\s+)?up|tl\s*;?\s*dr|recap|brief\s+me(?:\s+on)?"
              r"|(?:give\s+me\s+|what(?:'s|\s+is)\s+)?the\s+gist"
              r"|(?:give\s+me\s+|list\s+|what\s+are\s+)?(?:the\s+)?(?:key|main)\s+(?:points|takeaways)"
              r"|(?:give\s+me\s+|write\s+|make\s+)?an?\s+(?:short\s+|quick\s+|brief\s+)?(?:summary|recap|tl\s*;?\s*dr)"
              r"|what\s+are\s+the\s+(?:action\s+items|decisions|takeaways|next\s+steps)"
              r"|what\s+did\s+(?:they|we)\s+decide)")
_CUE_FILLER = (r"(?:\s+(?:(?:of|on|for|in|from)\s+)?(?:this|that|these|the\s+following|the\s+text\s+below|below|here))?"
               r"(?:\s+for\s+me)?(?:\s+(?:here|below))?(?:\s+please)?")
_CUE_END = r"[ \t]*(?:[:?!.]+|[-\u2013\u2014]+)?"
_LEADING_CUE = re.compile(rf"\s*{_CUE_LEAD}(?P<cue>{_CUE_WORDS}){_CUE_FILLER}(?P<end>{_CUE_END})(?:\s|$)",
                          re.IGNORECASE)
_CUE_LINE = re.compile(rf"{_CUE_LEAD}{_CUE_WORDS}{_CUE_FILLER}{_CUE_END}", re.IGNORECASE)
_LINE_COLON = re.compile(r":(?:[ \t]|$)")
_SENTENCE_END = re.compile(r"[.!?][\"')\]]*\s+\S")
_SPEAKER = re.compile(r"[A-Z][\w.'-]*(?: [A-Z][\w.'-]*){0,2}")


def find_cue(text: str) -> tuple[str, str] | None:
    """(instruction, source) when the message asks to summarize in words ("summarize this: ...", "tl;dr ...",
    "what are the action items here? ...", or "...\\nsummarize" after the text), else None. None too when
    the cue has no text with it ("summarize this"), so that case keeps its own route."""
    stripped = (text or "").strip()
    return _leading_cue(stripped) or _trailing_cue(stripped)


def _leading_cue(text: str) -> tuple[str, str] | None:
    match = _LEADING_CUE.match(text)
    if match is None:
        return None
    cut = match.end("end")
    if match.group("end").strip() or match.group(0).endswith("\n"):
        return _cut(text[:cut].rstrip(" \t:-\u2013\u2014"), text[cut:])
    first_line = text.split("\n", 1)[0]
    colon = _LINE_COLON.search(first_line, cut)
    extra = text[cut:colon.start()] if colon else ""
    if colon and _instruction_like(extra) and not _SPEAKER.fullmatch(extra.strip()):
        return _cut(text[:colon.start()], text[colon.end():])
    if "\n" in text and _instruction_like(first_line[cut:]):
        return _cut(first_line, text[len(first_line):])
    rest = text[cut:].strip()
    asks = match.group("cue").casefold().startswith("what")
    if asks or _words(rest) < CUED_MIN_SOURCE_WORDS or not _SENTENCE_END.search(rest):
        return None
    return _cut(text[:cut], rest)


def _trailing_cue(text: str) -> tuple[str, str] | None:
    head, newline, last = text.rpartition("\n")
    if not newline or not _CUE_LINE.fullmatch(last.strip()):
        return None
    return _cut(last, head)


def _instruction_like(extra: str) -> bool:
    """Words between the cue and its separator that read as more instruction ("this call, focus on pricing"),
    not as the start of the text ("tl;dr We met on Monday. ...", "tl;dr Priya: we ...")."""
    extra = extra.strip()
    if len(extra) > INSTRUCTION_MAX_CHARS or _words(extra) > CUE_INSTRUCTION_MAX_WORDS:
        return False
    return not (_SENTENCE_END.search(extra) or "?" in extra or _LINE_COLON.search(extra))


def _cut(instruction: str, source: str) -> tuple[str, str] | None:
    instruction, source = instruction.strip(), source.strip()
    return (instruction, source) if instruction and source else None


def _words(text: str) -> int:
    return len(text.split())
