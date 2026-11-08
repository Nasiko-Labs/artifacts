"""Pure: the pasted blocks. A block starts at a `Competitor:` line (or at a `Source:` line when the current
block already has a source); `Before:` / `After:` lines split the older and the current text. Text before the
first header is the user's question. The competitor name comes from the header only, never from the text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pricing
from records import UNNAMED, Block

_HEADER = re.compile(r"^\s*(?:[*_#>-]\s*)*(competitor|source|before|after)\s*:\s*(.*?)\s*$", re.IGNORECASE)
_TEAM = re.compile(r"\bteam\s*[:=]\s*([A-Za-z0-9][\w .&-]{0,39}?)\s*(?:[,.;\n]|$)", re.IGNORECASE)
_SOURCE_WORD = re.compile(r"[^a-z0-9 -]+")
MAX_NAME_CHARS = 80
MAX_SOURCE_CHARS = 30
MIN_PRICE_LINES = 2


@dataclass
class _Draft:
    competitor: str | None = None
    source: str | None = None
    parts: dict = field(default_factory=lambda: {"body": [], "before": [], "after": []})
    target: str = "body"


@dataclass(frozen=True)
class Parsed:
    blocks: tuple[Block, ...] = ()
    question: str = ""
    team: str | None = None


def _source(raw: str | None, body: str) -> str:
    word = _SOURCE_WORD.sub("", (raw or "").casefold()).strip()[:MAX_SOURCE_CHARS]
    if word.startswith("pric") or word in ("plans", "plan", "price"):
        return "pricing"
    if not word:
        lines = sum(1 for ln in body.splitlines() if pricing.is_price_line(ln))
        return "pricing" if lines >= MIN_PRICE_LINES else "changelog"
    return "changelog" if word.startswith(("change", "release")) else word


def _empty(draft: _Draft) -> bool:
    return not any(line.strip() for part in draft.parts.values() for line in part)


def _finish(draft: _Draft) -> Block | None:
    text = {k: "\n".join(v).strip() for k, v in draft.parts.items()}
    current = text["after"] or text["body"]
    if not current:
        return None
    name = " ".join((draft.competitor or "").split())[:MAX_NAME_CHARS]
    return Block(competitor=name or UNNAMED, source=_source(draft.source, current), body=current,
                 before=text["before"] or None, named=bool(name))


def parse(text: str) -> Parsed:
    question: list[str] = []
    drafts: list[_Draft] = []
    for line in str(text or "").splitlines():
        header = _HEADER.match(line)
        key = header.group(1).casefold() if header else ""
        value = header.group(2) if header else ""
        current = drafts[-1] if drafts else None
        if key == "competitor" and current is not None and current.competitor is None and _empty(current):
            current.competitor = value  # `Source:` came first in this block
        elif key == "competitor":
            drafts.append(_Draft(competitor=value))
        elif key == "source" and (current is None or current.source is not None):
            drafts.append(_Draft(source=value))
        elif key == "source":
            current.source = value
        elif key in ("before", "after") and current is not None:
            current.target = key
            if value:
                current.parts[key].append(value)
        elif current is not None:
            current.parts[current.target].append(line)
        else:
            question.append(line)
    blocks = tuple(b for b in (_finish(d) for d in drafts) if b is not None)
    asked = "\n".join(question).strip()
    team = _TEAM.search(asked)
    return Parsed(blocks=blocks, question=asked, team=team.group(1).strip() if team else None)
