"""Pure: finding the options in a brief and matching a user's words ("B", "option 2", "buying") to one.

Options come from an `Options:` section, from bullet / numbered / lettered / `Option A:` lines, or from
"X or Y" in the decision sentence; "whether to X" gives one option (the caller adds the status quo).
Keys are always A, B, C... in input order."""

from __future__ import annotations

import re
from dataclasses import dataclass

from text import normalise

MAX_OPTIONS = 5
NAME_MAX_WORDS = 5
STATUS_QUO = "Status quo (not changing)"
KEYS = "ABCDEFGHIJKLMNOPQRST"

_OPTION_LINE = re.compile(r"^\s*(?:[-*•+]\s+|(?:\d{1,2}|[A-Ea-e])[.)]\s+|\(\s*[A-Ea-e1-9]\s*\)\s*|"
                          r"option\s+[A-Za-z0-9]{1,2}\s*[:.)-]\s*)(?P<body>\S.*)$", re.IGNORECASE)
_INLINE_MARKER = re.compile(r"(?:^|[\s,;])\(?(?:option\s+)?[A-E1-9]\)\s*|(?:^|[\s,;])option\s+[A-E1-9]\s*[:.-]\s*",
                            re.IGNORECASE)
_NAME_END = re.compile(r"\s*(?::|\s-\s|\s–\s|\s—\s|\(|,|;|\.\s)|\s+(?:with|at|for|using|via|by|over|in|from|"
                       r"costing|which|that|so)\s+", re.IGNORECASE)
_OR = re.compile(r"\s+or\s+", re.IGNORECASE)
_WHETHER = re.compile(r"\bwhether\s+(?:or not\s+)?(?:to\s+|we\s+should\s+)?(?P<what>[^?.;\n]+)", re.IGNORECASE)
_DECISION_LEAD = re.compile(r"^(?:should\s+(?:we|i)\s+|do\s+we\s+|whether\s+to\s+|to\s+)", re.IGNORECASE)


@dataclass(frozen=True)
class Option:
    key: str
    name: str
    text: str
    added_by_me: bool = False


def _name(text: str) -> str:
    head = _NAME_END.split(text.strip(), maxsplit=1)[0].strip(" .")
    words = head.split()[:NAME_MAX_WORDS] or text.split()[:NAME_MAX_WORDS]
    name = " ".join(words)
    return name[:1].upper() + name[1:]


def make(texts: list[str], start: int = 0) -> tuple[Option, ...]:
    cleaned = [" ".join(t.split()).strip(" .;,") for t in texts]
    return tuple(Option(KEYS[start + i], _name(t), t) for i, t in enumerate(c for c in cleaned if c))


def from_lines(lines: list[str]) -> list[str]:
    """Bodies of the lines that are shaped like options (bullets, numbers, letters, `Option A:`)."""
    return [m.group("body").strip() for m in map(_OPTION_LINE.match, lines) if m]


def from_section(body: str) -> list[str]:
    """An `Options:` section: one option per line, or one line split on markers, ';', ' or ', ','."""
    lines = [line for line in body.splitlines() if line.strip()]
    if len(lines) > 1:
        bulleted = from_lines(lines)
        return bulleted if len(bulleted) >= len(lines) - 1 and bulleted else [line.strip() for line in lines]
    line = lines[0].strip() if lines else ""
    for splitter in (_INLINE_MARKER, re.compile(r";"), _OR, re.compile(r",")):
        parts = [p.strip(" .") for p in splitter.split(line) if p and p.strip(" .,;")]
        if len(parts) >= 2:
            return parts
    return [line] if line else []


def from_decision(decision: str) -> list[str]:
    """'Build or buy our billing engine' -> ['Build our billing engine', 'Buy our billing engine'];
    'whether to delay the launch' -> ['Delay the launch']."""
    what = decision.strip(" ?.")
    whether = _WHETHER.search(what)
    if whether:
        what = whether.group("what").strip()
    what = _DECISION_LEAD.sub("", what).strip()
    parts = [p.strip(" ,") for p in _OR.split(what) if p.strip(" ,")]
    if len(parts) == 2 and len(parts[0].split()) == 1 and len(parts[1].split()) > 1:
        tail = " ".join(parts[1].split()[1:])
        parts = [f"{parts[0]} {tail}", parts[1]]
    if len(parts) >= 2:
        return [p[:1].upper() + p[1:] for p in parts]
    if whether and what:
        return [what[:1].upper() + what[1:]]
    return []


def with_status_quo(options: tuple[Option, ...]) -> tuple[Option, ...]:
    if len(options) != 1:
        return options
    return (*options, Option(next_key(options), STATUS_QUO, "Keep things as they are; do not make the change.", True))


def next_key(options: tuple[Option, ...] | list[Option]) -> str:
    used = {o.key for o in options}
    return next(k for k in KEYS if k not in used)


def match(phrase: str, options: tuple[Option, ...] | list[Option]) -> str:
    """The key of the option `phrase` names ("B", "option 2", "buy", "buying"), else ""."""
    norm = normalise(phrase).strip(" .!?")
    norm = re.sub(r"^(?:option|choice)\s+", "", norm)
    tokens = norm.split()
    if not tokens:
        return ""
    keys = {o.key.casefold(): o.key for o in options}
    if tokens[0].strip(".,)") in keys:
        return keys[tokens[0].strip(".,)")]
    if tokens[0].isdigit() and 1 <= int(tokens[0]) <= len(options):
        return options[int(tokens[0]) - 1].key
    named = [o.key for o in options if normalise(o.name) and (normalise(o.name) in norm or norm in normalise(o.name))]
    if len(named) == 1:
        return named[0]
    stem = tokens[0][:3]
    prefixed = [o.key for o in options if len(stem) == 3 and normalise(o.name)[:3] == stem]
    return prefixed[0] if len(prefixed) == 1 else ""
