"""Pure: one pasted line -> one record, by shape. PR lines carry a `#123`, a pull-request link, an author or a
conventional-commit prefix; praise has a speaker and words of thanks; a shipped feature names its owners."""

from __future__ import annotations

import re
from datetime import date

import dates
from records import PR, PRAISE, SHIPPED, Record

URL = re.compile(r"https?://[^\s)>\]]+")
PR_NUMBER = re.compile(r"(?:(?<=\s)|^|(?<=\())#(\d{1,7})\b|/(?:pull|merge_requests)/(\d{1,7})\b")
CONVENTIONAL = re.compile(r"^(?:feat|fix|chore|docs|refactor|perf|test|build|ci|style|revert)(?:\([^)]*\))?!?:\s*",
                          re.IGNORECASE)
_NAME = r"@[\w.-]+|[A-Z][\w'.-]+(?:\s+[A-Z][\w'-]+){0,2}"
AUTHOR = re.compile(rf"\b(?i:author|opened by|user|by)\s*[:=]?\s*(?P<who>{_NAME})")
HANDLE = re.compile(r"(?<![\w/])@[A-Za-z0-9][\w.-]*")
PR_SHAPE = re.compile(r"(?:^|\s|\()#\d+\b|/pull/\d+|/merge_requests/\d+|\b(?:author|opened by)\s*[:=]",
                      re.IGNORECASE)
PRAISE_WORDS = re.compile(r"\b(?:thanks|thank you|thx|kudos|shout-?out|props|great|amazing|awesome|appreciate[ds]?|"
                          r"nice work|well done|legend|you rock|grateful|huge help|saved us)\b", re.IGNORECASE)
SHIPPED_SHAPE = re.compile(r"^(?:shipped|launched|released)\b|\bowners?\s*:", re.IGNORECASE)
OWNERS = re.compile(r"[\(\[]?\bowners?\s*:\s*(?P<who>[^)\]]+)[\)\]]?", re.IGNORECASE)
BY_OWNERS = re.compile(r"\bby\s+(?P<who>(?:@[\w.-]+|[A-Z][\w'-]+)(?:[\s,&]+(?:and\s+)?(?:@[\w.-]+|[A-Z][\w'-]+))*)")
SPEAKER = re.compile(r"^(?:\[(?P<d1>[^\]]{3,30})\]\s*)?(?P<who>@?[\w.'-]+(?:\s+[\w.'-]+){0,2})\s*"
                     r"(?:\((?P<d2>[^)]{3,30})\))?\s*:\s*(?P<msg>.+)$")
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_SEPARATORS = re.compile(r"^[\s\-–—|,:;()\[\]]+|[\s\-–—|,:;()\[\]]+$")
_EMPTY_BRACKETS = re.compile(r"\(\s*[,;]?\s*\)|\[\s*\]")
NOT_SPEAKERS = frozenset({"pr", "prs", "praise", "kudos", "shipped", "note", "fix", "feat", "thanks", "http", "https"})


def strip_bullet(line: str) -> str:
    return BULLET.sub("", line).strip()


def _cut(text: str, spans: list[tuple[int, int]]) -> str:
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + " " + text[end:]
    text = _EMPTY_BRACKETS.sub(" ", " ".join(text.split()))
    return _SEPARATORS.sub("", " ".join(text.split()))


def clean_title(title: str) -> str:
    """The PR title without its conventional-commit prefix, first letter capitalised (the fallback example)."""
    text = CONVENTIONAL.sub("", (title or "").strip()).strip()
    return (text[:1].upper() + text[1:]) if text else ""


def split_names(text: str) -> tuple[str, ...]:
    parts = re.split(r"\s*(?:,|&|/|;|\band\b)\s*", text or "")
    return tuple(p.strip(" .") for p in parts if p.strip(" .") and (p.strip()[0].isupper() or p.strip()[0] == "@"))


def classify(line: str) -> str | None:
    if SHIPPED_SHAPE.search(line):
        return SHIPPED
    if PRAISE_WORDS.search(line) and not CONVENTIONAL.match(line) and not re.search(r"\bauthor\s*[:=]", line, re.IGNORECASE):
        return PRAISE
    if PR_SHAPE.search(line) or CONVENTIONAL.match(line):
        return PR
    return None


def pr_line(line: str, today: date) -> Record:
    spans: list[tuple[int, int]] = []
    url_m = URL.search(line)
    num_m = PR_NUMBER.search(line)
    number = (num_m.group(1) or num_m.group(2)) if num_m else ""
    iso, span = dates.find(line, today)
    author_m = AUTHOR.search(line) or HANDLE.search(line)
    author = (author_m.group("who") if author_m and author_m.re is AUTHOR else
              author_m.group(0) if author_m else "")
    for m in (url_m, author_m):
        if m:
            spans.append(m.span())
    if span:
        spans.append(span)
    spans += [m.span() for m in re.finditer(r"(?:(?<=\s)|^|(?<=\())#\d{1,7}\b", line)]
    spans += [m.span() for m in re.finditer(r"\bmerged(?: on)?\b", line, re.IGNORECASE)]
    return Record("", PR, line, title=_cut(line, spans), author=author, date=iso,
                  url=url_m.group(0) if url_m else "", number=number)


def praise_line(line: str, today: date) -> Record:
    match = SPEAKER.match(line)
    speaker, body, when = "", line, ""
    if match and _is_speaker(match["who"]):
        speaker, body, when = match["who"], match["msg"].strip(), match["d1"] or match["d2"] or ""
    iso, _ = dates.find(when, today) if when else (None, None)
    if not iso:
        iso, _ = dates.find(body, today)
    return Record("", PRAISE, line, speaker=speaker, body=body, date=iso)


def _is_speaker(who: str) -> bool:
    tokens = who.split()
    if who.casefold() in NOT_SPEAKERS or PRAISE_WORDS.search(who):
        return False
    return all(t[0].isupper() or t[0] == "@" for t in tokens)


def shipped_line(line: str, today: date) -> Record:
    text = re.sub(r"^(?:shipped|launched|released)\s*[:\-–—]?\s*", "", line, flags=re.IGNORECASE)
    spans: list[tuple[int, int]] = []
    owners: tuple[str, ...] = ()
    m = OWNERS.search(text) or BY_OWNERS.search(text)
    if m:
        owners, spans = split_names(m["who"]), [m.span()]
    url_m = URL.search(text)
    refs = tuple(f"#{n}" for n in re.findall(r"(?:(?<=\s)|^|(?<=\())#(\d{1,7})\b", text))
    refs += tuple(u.rstrip("/").casefold() for u in URL.findall(text))
    refs += tuple(f"#{n}" for n in re.findall(r"/(?:pull|merge_requests)/(\d{1,7})\b", text))
    iso, span = dates.find(text, today)
    spans += [x for x in (span, url_m.span() if url_m else None) if x]
    spans += [x.span() for x in re.finditer(r"(?:(?<=\s)|^|(?<=\())#\d{1,7}\b", text)]
    return Record("", SHIPPED, line, title=_cut(text, spans), owners=owners, date=iso,
                  url=url_m.group(0) if url_m else "", refs=tuple(dict.fromkeys(refs)))


def parse_line(kind: str, line: str, today: date) -> Record:
    return {PR: pr_line, PRAISE: praise_line, SHIPPED: shipped_line}[kind](line, today)
