"""Pure: the faithfulness and format checks every brief passes before it is shown.

1. Each cite must be found (normalised) in its section, else re-pointed or removed; a line left
   with no cite is dropped.
2. Numbers, money, dates, months, weekdays, emails, URLs and capitalised names in a line's wording
   must appear in that line's cited sections, else the line is dropped (the headline is repaired
   once, then falls back to the top risk's text).
3. A decision whose snippets say it was already decided is dropped.
4. Owner / by are kept only near the snippet in a cited section.
5. Caps: headline <= 20 words / 140 chars; <= 5 risks (high first, then paste order); <= 5
   decisions; <= 35 words a line; <= 300 words for the three parts together.
Every drop is counted in a note; `problems` tells the repair call what to fix.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, replace

from brief import SEVERITIES, Brief, Line
from grounding import Sources
from render import body_words, clean
from sections import Section

HEADLINE_MAX_WORDS = 20
HEADLINE_MAX_CHARS = 140
LINE_MAX_WORDS = 35
MAX_RISKS = 5
MAX_DECISIONS = 5
PAGE_MAX_WORDS = 300
OWNER_DISTANCE_CHARS = 400

_CLOSED = re.compile(r"\b(?:decided|approved|agreed|signed)\b", re.IGNORECASE)
_OPEN = re.compile(r"\?|\b(?:pending|needs?|awaiting|tbd|decide|to be decided)\b", re.IGNORECASE)
_CLAUSE_BREAK = re.compile(r"[,;:—–]\s|\s-\s|\s(?:and|but|while|because)\s")


@dataclass(frozen=True)
class Checked:
    brief: Brief
    problems: tuple[str, ...]


def check(brief: Brief, sections: Sequence[Section]) -> Checked:
    sources = Sources(sections)
    problems: list[str] = []
    risks, untraced_r = _ground_lines(brief.risks, sources, "risk", problems)
    decisions, untraced_d = _ground_lines(brief.decisions, sources, "decision", problems)
    open_decisions = tuple(d for d in decisions if is_open(d))
    closed = len(decisions) - len(open_decisions)
    decisions = tuple(_owner_by(d, sources) for d in open_decisions)
    risks = tuple(sorted(risks, key=lambda r: (SEVERITIES.index(r.severity), sources.rank(r.cites[0]))))
    risks, decisions = _cut_lines(risks, problems), _cut_lines(decisions, problems)
    capped = max(0, len(risks) - MAX_RISKS) + max(0, len(decisions) - MAX_DECISIONS)
    headline = _headline(brief.headline, sources, risks, decisions, problems)
    fitted, trimmed = _fit(replace(brief, headline=headline, risks=risks[:MAX_RISKS],
                                   decisions=decisions[:MAX_DECISIONS]))
    notes = _notes(untraced_r + untraced_d, closed, capped + trimmed)
    return Checked(replace(fitted, notes=notes), tuple(problems))


def _ground_lines(lines: Sequence[Line], sources: Sources, kind: str, problems: list[str]) -> tuple[tuple, int]:
    kept, dropped = [], 0
    for line in lines:
        text = clean(line.text)
        cites = sources.ground(line.cites)
        missing = sources.ungrounded(text, cites) if cites else []
        if not cites:
            problems.append(f"The {kind} \"{text[:80]}\" has no snippet found word for word in its section.")
        elif missing:
            problems.append(f"The {kind} \"{text[:80]}\" uses {', '.join(missing[:5])}, not in its cited sections.")
        if not cites or missing:
            dropped += 1
            continue
        kept.append(replace(line, text=text, cites=cites))
    return tuple(kept), dropped


def is_open(decision: Line) -> bool:
    snippets = " ".join(c.snippet for c in decision.cites)
    return not _CLOSED.search(snippets) or bool(_OPEN.search(snippets))


def _owner_by(decision: Line, sources: Sources) -> Line:
    def near(value: str) -> str:
        return value if any(sources.near(value, c, OWNER_DISTANCE_CHARS) for c in decision.cites) else ""

    return replace(decision, owner=near(decision.owner), by=near(decision.by))


def _headline(headline: Line | None, sources: Sources, risks: tuple, decisions: tuple, problems: list[str]) -> Line | None:
    if headline is not None:
        text = clean(headline.text)
        cites = sources.ground(headline.cites)
        missing = sources.ungrounded(text, cites) if cites else ["(no cite found)"]
        if not missing:
            if len(text.split()) > HEADLINE_MAX_WORDS or len(text) > HEADLINE_MAX_CHARS:
                problems.append(f"The headline is over {HEADLINE_MAX_WORDS} words or {HEADLINE_MAX_CHARS} characters.")
                text = cut(text, HEADLINE_MAX_WORDS, HEADLINE_MAX_CHARS)
            return replace(headline, text=text, cites=cites)
        problems.append(f"The headline cannot be traced to its cited sections ({', '.join(missing[:5])}).")
    top = (risks or decisions or (None,))[0]
    if top is None:
        return None
    return Line(cut(top.text, HEADLINE_MAX_WORDS, HEADLINE_MAX_CHARS), top.cites)


def _cut_lines(lines: tuple[Line, ...], problems: list[str]) -> tuple[Line, ...]:
    out = []
    for line in lines:
        if len(line.text.split()) > LINE_MAX_WORDS:
            problems.append(f"The line \"{line.text[:60]}…\" is over {LINE_MAX_WORDS} words.")
            line = replace(line, text=cut(line.text, LINE_MAX_WORDS))
        out.append(line)
    return tuple(out)


def cut(text: str, max_words: int, max_chars: int | None = None) -> str:
    """At most `max_words` words (and `max_chars`), ended at a clause boundary when one is late enough."""
    words = text.split()
    if len(words) <= max_words and (max_chars is None or len(text) <= max_chars):
        return text
    head = " ".join(words[:max_words])
    if max_chars is not None and len(head) > max_chars - 1:
        head = head[:max_chars - 1].rsplit(" ", 1)[0]
    breaks = [m.start() for m in _CLAUSE_BREAK.finditer(head) if m.start() >= len(head) // 2]
    return (head[:breaks[-1]] if breaks else head).rstrip(" ,;:-—–") + "…"


def _fit(brief: Brief) -> tuple[Brief, int]:
    """Drop the lowest-severity risk, then the last decision, in turn, until the page fits."""
    dropped, drop_risk = 0, True
    while body_words(brief) > PAGE_MAX_WORDS and (brief.risks or brief.decisions):
        if (drop_risk and brief.risks) or not brief.decisions:
            brief = brief.with_lines(risks=brief.risks[:-1])
        else:
            brief = brief.with_lines(decisions=brief.decisions[:-1])
        dropped, drop_risk = dropped + 1, not drop_risk
    return brief, dropped


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _notes(untraced: int, closed: int, trimmed: int) -> tuple[str, ...]:
    notes = []
    if untraced:
        notes.append(f"{_plural(untraced, 'line')} left out: I couldn't trace them to your pasted text.")
    if closed:
        notes.append(f"{_plural(closed, 'decision')} left out: the inputs say it was already made.")
    if trimmed:
        notes.append(f"{_plural(trimmed, 'line')} left out to keep the brief to one page.")
    return tuple(notes)
