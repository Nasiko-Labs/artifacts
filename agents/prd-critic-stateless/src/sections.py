"""Pure: number the PRD's lines, find its headings and map them onto the rubric's seven required sections.

A line is a heading when it is a markdown heading (`## Goals`), a bold line (`**Goals**`), a short numbered
line whose words name a section (`2. Success metrics`), or a label line (`Problem: checkout is slow`, whose
text after the colon is section body). A section is *present* when its body has at least 15 words; a heading
with less under it is *empty*. The model only locates sections when headings fail (see review.py)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

REQUIRED = ("problem", "users", "goals", "non_goals", "requirements", "metrics", "open_questions")
LABELS = {"problem": "Problem", "users": "Users", "goals": "Goals", "non_goals": "Non-goals",
          "requirements": "Requirements", "metrics": "Metrics", "open_questions": "Open questions"}
ALIASES = {
    "problem": ("problem", "problem statement", "background", "context", "motivation", "opportunity"),
    "users": ("users", "user", "personas", "audience", "target customers", "target users", "stakeholders"),
    "goals": ("goals", "goal", "objectives", "outcomes", "success criteria", "goals and objectives"),
    "non_goals": ("non-goals", "non goals", "nongoals", "out of scope", "not in scope", "exclusions"),
    "requirements": ("requirements", "functional requirements", "scope", "features", "user stories",
                     "requirement"),
    "metrics": ("metrics", "success metrics", "kpis", "kpi", "measures", "key metrics"),
    "open_questions": ("open questions", "questions", "risks and open issues", "tbd", "open issues"),
}
OPTIONAL = frozenset({"timeline", "milestones", "risks", "rollout", "rollout plan", "launch plan", "release plan",
                      "dependencies", "appendix", "references", "faq", "design", "analytics", "launch", "pricing",
                      "competition", "competitive landscape", "alternatives considered", "summary", "overview",
                      "tl dr", "tldr", "assumptions", "constraints", "go to market"})
MIN_SECTION_WORDS = 15
SHORT_SECTION_MIN_WORDS = {"non_goals": 5}   # one plain sentence ("SSO is out of scope") is a real Non-goals
MAX_LINE_CHARS = 400

_ALIAS_TO_KEY = {alias: key for key, names in ALIASES.items() for alias in names}
_MD = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_BOLD = re.compile(r"^\s*(?:\*\*|__)(.+?)(?:\*\*|__)\s*:?\s*$")
_NUMBERED = re.compile(r"^\s*(?:\d{1,2}|[ivx]{1,4})[.)]\s+(.+?)\s*:?\s*$", re.IGNORECASE)
_LABEL = re.compile(r"^\s*(?:\*\*|__)?([A-Za-z][A-Za-z /&-]{1,40}?)(?:\*\*|__)?\s*:(?:\*\*|__)?\s*(.*)$")
_PAREN = re.compile(r"\([^)]*\)")


@dataclass(frozen=True)
class Heading:
    line: int            # 1-based PRD line number
    title: str
    key: str | None      # a required section, or None for any other heading (Timeline, Risks...)
    rest: str = ""       # body text on a label line ("Problem: <rest>")
    level: int = 7       # markdown depth 1-6; bold / numbered / label lines are 7

    def closes(self, opener: Heading) -> bool:
        """Whether this heading ends the section `opener` started: any section name (required or optional)
        does; another heading only when it is at least as high as the opener (`### P0` stays inside)."""
        return self.key is not None or normalise_title(self.title) in OPTIONAL or self.level <= opener.level


@dataclass(frozen=True)
class Section:
    key: str
    heading: str
    start: int           # first body line (1-based, inclusive)
    end: int             # last body line (inclusive); end < start = no body
    words: int
    by: str = "heading"  # heading | model
    rest: str = ""

    @property
    def present(self) -> bool:
        return self.words >= SHORT_SECTION_MIN_WORDS.get(self.key, MIN_SECTION_WORDS)


@dataclass(frozen=True)
class Layout:
    lines: tuple[str, ...]
    headings: tuple[Heading, ...] = ()
    sections: dict[str, Section] = field(default_factory=dict)

    def body(self, key: str) -> list[tuple[int, str]]:
        """(line number, text) of a section's body; a label line's own text comes first."""
        section = self.sections.get(key)
        if section is None:
            return []
        own = [(section.start - 1, section.rest)] if section.rest else []
        return own + [(n, self.lines[n - 1]) for n in range(section.start, section.end + 1)]


def number_lines(text: str) -> tuple[str, ...]:
    """The PRD's non-blank lines, trailing space removed (index 0 = L1). Overlong lines stay whole."""
    return tuple(line.rstrip() for line in (text or "").splitlines() if line.strip())


def numbered_block(lines: tuple[str, ...] | list[str]) -> str:
    return "\n".join(f"L{n}: {line[:MAX_LINE_CHARS]}" for n, line in enumerate(lines, start=1))


def normalise_title(title: str) -> str:
    text = _PAREN.sub(" ", title.replace("&", " and ")).casefold()
    text = re.sub(r"^\s*(?:\d{1,2}|[ivx]{1,4})[.)]\s+", "", text)
    text = re.sub(r"[^a-z0-9 -]+", " ", text)
    return " ".join(text.replace(" - ", " ").split()).strip(" -")


def key_of(title: str) -> str | None:
    name = normalise_title(title)
    if name in _ALIAS_TO_KEY:
        return _ALIAS_TO_KEY[name]
    first = re.split(r" and |/", name)[0].strip()
    return _ALIAS_TO_KEY.get(first)


def heading_of(n: int, line: str) -> Heading | None:
    markdown = _MD.match(line)
    if markdown:
        title = markdown.group(1).strip().strip("*_").rstrip(":").strip()
        return Heading(n, title, key_of(title), level=len(line.strip()) - len(line.strip().lstrip("#")))
    bold = _BOLD.match(line)
    if bold:
        title = bold.group(1).strip().rstrip(":").strip()
        return Heading(n, title, key_of(title))
    numbered = _NUMBERED.match(line)
    if numbered and len(numbered.group(1).split()) <= 6 and key_of(numbered.group(1)):
        return Heading(n, numbered.group(1).rstrip(":"), key_of(numbered.group(1)))
    label = _LABEL.match(line)
    if label and key_of(label.group(1)):
        return Heading(n, label.group(1).strip(), key_of(label.group(1)), label.group(2).strip().strip("*_ "))
    return None


def find_headings(lines: tuple[str, ...]) -> tuple[Heading, ...]:
    return tuple(h for n, line in enumerate(lines, start=1) if (h := heading_of(n, line)) is not None)


def layout(lines: tuple[str, ...]) -> Layout:
    """Sections by heading: each runs to the next heading. A repeated section keeps its first heading."""
    headings = find_headings(lines)
    sections: dict[str, Section] = {}
    for i, h in enumerate(headings):
        if h.key is None or h.key in sections:
            continue
        closer = next((c for c in headings[i + 1:] if c.closes(h)), None)
        end = closer.line - 1 if closer else len(lines)
        words = len(h.rest.split()) + sum(len(lines[n - 1].split()) for n in range(h.line + 1, end + 1))
        sections[h.key] = Section(h.key, h.title, h.line + 1, end, words, "heading", h.rest)
    return Layout(lines, headings, sections)


def with_ranges(base: Layout, ranges: dict[str, list[tuple[int, int]]]) -> Layout:
    """Adds model-located sections for keys headings did not find. A range must lie inside the PRD and not
    overlap a heading section or an earlier accepted range; anything else is ignored."""
    taken = [(s.start - 1 if s.rest else s.start, s.end) for s in base.sections.values()]
    sections = dict(base.sections)
    for key in REQUIRED:
        if key in sections:
            continue
        for start, end in ranges.get(key, ()):
            if not (1 <= start <= end <= len(base.lines)) or any(start <= b and a <= end for a, b in taken):
                continue
            words = sum(len(base.lines[n - 1].split()) for n in range(start, end + 1))
            sections[key] = Section(key, LABELS[key], start, end, words, "model")
            taken.append((start, end))
            break
    return Layout(base.lines, base.headings, sections)


def found_by_heading(lay: Layout) -> int:
    return sum(1 for s in lay.sections.values() if s.by == "heading")
