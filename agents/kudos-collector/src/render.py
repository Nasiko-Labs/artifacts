"""Pure: the markdown evidence packets, from templates in code. The model writes no prose about people.

People are in alphabetical order (or the order the user named them), never sorted by any figure. Tool
text (titles, quotes) is shown escaped, never as markup; links only when they are http(s) URLs.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime

import prompts
from records import NotablePR, PersonPacket, PraiseItem, ShippedItem
from request import clean_label
from window import Window

HEADER_TAIL = "Evidence only, not a rating."
PASTED = "pasted by you"
_URL = re.compile(r"^https?://[^\s<>()\[\]]+$")
_MD = re.compile(r"([\\`*_\[\]<>|#])")
RANKING_WORDS = re.compile(r"\b(?:rank\w*|best|worst|top|most|least|leader ?board|scor(?:e|es|ed|ing)|"
                           r"outperform\w*|star performer)\b", re.IGNORECASE)
_QUOTED = re.compile(r"\"[^\"]*\"|“[^”]*”")
_LINK = re.compile(r"https?://\S+")


def report(window: Window, packets: Sequence[PersonPacket], covered: frozenset[str], notes: Sequence[str]) -> str:
    parts = [_header(window, covered), *(f"_{n}_" for n in notes), *(_packet(p) for p in packets),
             f"_{prompts.FRAMING_LINE}_"]
    return "\n\n".join(p for p in parts if p)


def judges_people(text: str) -> bool:
    """Ranking or scoring words in text the agent wrote (quotes and links are the tools' words, ignored)."""
    return bool(RANKING_WORDS.search(_LINK.sub(" ", _QUOTED.sub(" ", text or ""))))


def day(value: date | datetime) -> str:
    return f"{value:%b} {value.day}, {value.year}"


def _header(window: Window, covered: frozenset[str]) -> str:
    read = {frozenset({"code", "chat"}): "code host and chat read", frozenset({"code"}): "code host read",
            frozenset({"chat"}): "chat read", frozenset(): "nothing read from tools"}[covered]
    clamp = " The window was clamped to 14 to 180 days." if window.clamped else ""
    return f"**Kudos evidence, {day(window.start)} to {day(window.last_day)}** ({read}). {HEADER_TAIL}{clamp}"


def _packet(p: PersonPacket) -> str:
    counts = (f"{p.prs_merged} PRs merged, {len(p.notable)} notable, {len(p.praise)} praise messages from "
              f"{p.distinct_praisers} {'person' if p.distinct_praisers == 1 else 'people'}")
    lines = [f"**{clean_label(p.person.label)}**: {counts}"]
    if p.notable:
        lines += ["Notable", *(_notable(n) for n in p.notable)]
    if p.shipped:
        lines.append("Shipped: " + "; ".join(_shipped(s) for s in p.shipped))
    if p.praise:
        lines += ["Praise", *(_praise(q) for q in p.praise)]
    lines += [f"- _{g}_" for g in p.gaps]
    return "\n".join(lines)


def _notable(n: NotablePR) -> str:
    pr = n.pr
    when = f" ({day(pr.merged_at)})" if pr.merged_at else ""
    title = f" {escape(pr.title)}" if pr.title else ""
    return f"- {_ref(pr.number, pr.url)}{title}{when}: {'; '.join(n.reasons)}."


def _shipped(s: ShippedItem) -> str:
    where = f" in release {escape(s.release)}" if s.release else ""
    return f"{_ref(s.pr.number, s.pr.url)}{where}"


def _praise(q: PraiseItem) -> str:
    where = PASTED if q.pasted else f"#{escape(q.channel)}"
    when = f", {day(q.ts)}" if q.ts else ""
    link = f" {_link(q.permalink)}" if q.permalink else ""
    return f"- {escape(q.author)} in {where}{when}: \"{escape(q.quote)}\"{link}"


def _ref(number: int, url: str) -> str:
    return f"[#{number}]({url})" if _URL.match(url or "") else f"#{number}"


def _link(url: str) -> str:
    return f"<{url}>" if _URL.match(url or "") else ""


def escape(text: str) -> str:
    return _MD.sub(r"\\\1", " ".join((text or "").split()))
