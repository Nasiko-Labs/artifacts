"""Pure: what this message asks for, decided in code. A request's cues are read from its first line (the
"head"); everything after it is the payload, so words inside a pasted text never change the route.

Kinds: intro | decline | split | append | end | merge | show_part | list_parts | as_json | resplit |
merge_more | merge_notes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import smalltalk
from split import SplitOptions

HEAD_MAX_CHARS = 400
STEP_MAX_CHARS = 500
THOUSAND = 1000

_SPLIT = re.compile(r"\b(?:split|chunk|cut|divide|break (?:\w+ )?(?:in)?to)\b")
_MERGE = re.compile(r"\b(?:merge|combine|join)\b")
_PART_OF = re.compile(r"\bpart (\d+) of (\d+)\b")
_APPEND = re.compile(r"\b(?:append|more coming|more to come|to be continued|there'?s more)\b")
_END = re.compile(r"^(?:end|that'?s all|that is all|that'?s it|done|that'?s everything|finished|no more)\b"
                  r"|\blast (?:part|piece)\b")
_SHOW = re.compile(r"^(?:show|print|give|display|paste)(?: me)?(?: the)? (?:part|chunk|piece) (\d+)\b")
_LIST = re.compile(r"^(?:list|show)(?: me)?(?: the| all(?: the)?)? (?:parts|chunks|pieces)\b")
_AS_JSON = re.compile(r"^(?:as|in|show(?: it)? as|give(?: me)? the) json\b")
_RESPLIT = re.compile(r"^(?:split|cut|chunk)(?: it| this)? again\b|^re-?split\b")
_MERGE_MORE = re.compile(r"^(?:merge|combine|add)(?: it)? (?:with|to) (?:this|these)\b|^add (?:this|these)\b")
_MERGE_NOTES = re.compile(r"\bmerge notes\b")
_PARTS = re.compile(r"\b(\d{1,3})\s+(?:equal\s+)?(?:parts|pieces|chunks)\b")
_CHARS = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*(?:chars?|characters?)\b")
_TOKENS = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k)?\s*tokens?\b")
_OVERLAP = re.compile(r"\boverlap(?:ping)?(?: of| by)?\s+(\d[\d,]*)\s*([a-z]+)")
_STEP = re.compile(r"\bfor (?:each|every) (?:part|chunk|piece)\s*[:,\-]?\s*(.+)$")
_STRATEGY = (("turns", re.compile(r"\bby (?:speaker )?(?:turns?|speakers?)\b")),
             ("headings", re.compile(r"\bby (?:headings?|sections?)\b")),
             ("paragraphs", re.compile(r"\bby paragraphs?\b")),
             ("sentences", re.compile(r"\bby sentences?\b")))


@dataclass(frozen=True)
class Request:
    kind: str
    payload: str = ""
    opts: SplitOptions = field(default_factory=SplitOptions)
    number: int = 0
    reply: str = ""


def classify(text: str) -> Request:
    canned = smalltalk.reply_to(text)
    if canned is not None:
        return Request("intro", reply=canned)
    head, payload = head_and_payload(text)
    low = " ".join(head.casefold().split())
    opts = parse_options(head)
    if m := _SHOW.match(low):
        return Request("show_part", number=int(m.group(1)))
    for kind, pattern in (("list_parts", _LIST), ("as_json", _AS_JSON), ("resplit", _RESPLIT)):
        if pattern.match(low):
            return Request(kind, opts=opts)
    if _MERGE_NOTES.search(low):
        return Request("merge_notes")
    if _MERGE_MORE.match(low):
        return Request("merge_more", payload=text)
    merge_cue, split_cue = _MERGE.search(low), _SPLIT.search(low)
    if merge_cue and (split_cue is None or merge_cue.start() < split_cue.start()):
        return Request("merge", payload=text)
    part_of = _PART_OF.search(low)
    if part_of and int(part_of.group(1)) >= int(part_of.group(2)) or _END.search(low):
        return Request("end", payload=payload, opts=opts)
    if part_of or _APPEND.search(low):
        return Request("append", payload=payload, opts=opts)
    if split_cue:
        return Request("split", payload=payload, opts=opts)
    return Request("decline")


def head_and_payload(text: str) -> tuple[str, str]:
    """(the request line, the text after it). A multi-line message's first line is the head when it is
    short; a one-line message is cut after its last ': ' within the first HEAD_MAX_CHARS."""
    stripped = (text or "").strip()
    first, sep, rest = stripped.partition("\n")
    if sep and len(first) <= HEAD_MAX_CHARS:
        return first.strip(), rest.strip("\n").strip()
    cut = stripped.rfind(": ", 0, HEAD_MAX_CHARS)
    if cut == -1:
        return stripped[:HEAD_MAX_CHARS], stripped[HEAD_MAX_CHARS:].strip() if len(stripped) > HEAD_MAX_CHARS else ""
    return stripped[:cut + 1], stripped[cut + 2:].strip()


def parse_options(head: str) -> SplitOptions:
    low = " ".join(head.casefold().split())
    overlap_units = overlap_chars = 0
    if m := _OVERLAP.search(low):
        amount = int(_number(m.group(1)))
        overlap_chars = amount if m.group(2).startswith("char") else 0
        overlap_units = 0 if overlap_chars else amount
        low = low[:m.start()] + low[m.end():]
    step = ""
    if m := _STEP.search(" ".join(head.split())):
        step = m.group(1).strip().rstrip(":").strip()[:STEP_MAX_CHARS]
    size = None
    if m := _CHARS.search(low):
        size = int(_number(m.group(1)) * (THOUSAND if m.group(2) else 1))
    elif m := _TOKENS.search(low):
        size = int(_number(m.group(1)) * (THOUSAND if m.group(2) else 1) * 4)
    parts = int(m.group(1)) if (m := _PARTS.search(low)) else None
    strategy = next((name for name, pattern in _STRATEGY if pattern.search(low)), None)
    return SplitOptions(size=size, parts=None if size else parts, strategy=strategy,
                        overlap_units=overlap_units, overlap_chars=overlap_chars, step=step)


def _number(raw: str) -> float:
    value = float(raw.replace(",", ""))
    return int(value) if value.is_integer() else value
