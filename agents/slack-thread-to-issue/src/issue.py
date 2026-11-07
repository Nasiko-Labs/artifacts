"""Pure: everything about a proposed issue that code decides. The title's shape, a due date only from an
explicit date in the action quote, labels and project only when they exist in the tracker and appear in the
thread, the description template, the duplicate test and the order of proposals. Priority is never set."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date

from models import Finding, IssueDraft, Named
from textcheck import parse_day

MAX_TITLE_CHARS = 80
MAX_LABELS = 3
DUPLICATE_JACCARD = 0.7
DUP_WORDS = 8

TITLE_VERBS = frozenset({
    "add", "allow", "analyze", "analyse", "audit", "build", "change", "check", "clean", "configure", "create",
    "debug", "define", "delete", "deliver", "deploy", "design", "document", "draft", "enable", "evaluate",
    "export", "extend", "file", "fix", "follow", "handle", "implement", "import", "improve", "increase",
    "investigate", "make", "migrate", "move", "optimize", "optimise", "prepare", "provide", "reduce", "refactor",
    "remove", "rename", "replace", "resolve", "restore", "review", "rewrite", "schedule", "send", "set", "share",
    "ship", "show", "speed", "support", "test", "track", "update", "upgrade", "validate", "verify", "write",
})
_KIND_VERB = {"bug": "Fix", "task": "Complete", "docs": "Update", "follow-up": "Follow up on"}

_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DAY_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + "|".join(m[:3] for m in _MONTHS) + r")[a-z]*\b",
                        re.IGNORECASE)
_MONTH_DAY = re.compile(r"\b(" + "|".join(m[:3] for m in _MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b",
                        re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")


def title_problem(title: str) -> bool:
    """True when the title breaks the rules: empty, over 80 chars, trailing period, no leading verb."""
    words = title.strip().split()
    return (not words or len(title.strip()) > MAX_TITLE_CHARS or title.strip().endswith(".")
            or words[0].casefold().strip(":,") not in TITLE_VERBS)


def fix_title(title: str, kind: str) -> str:
    """The code fallback after one repair: a leading verb from the kind, no trailing period, at most 80
    characters cut at a word boundary."""
    text = " ".join(title.split()).rstrip(". ")
    words = text.split()
    if not words or words[0].casefold().strip(":,") not in TITLE_VERBS:
        verb = _KIND_VERB.get(kind, "Handle")
        first = words[0] if words else ""
        lowered = first[:1].lower() + first[1:] if first[1:2].islower() else first
        text = f"{verb} {' '.join([lowered, *words[1:]])}" if words else f"{verb} the agreed action"
    if len(text) <= MAX_TITLE_CHARS:
        return text
    cut = text[:MAX_TITLE_CHARS + 1].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-.")[:MAX_TITLE_CHARS]


def due_from_quote(quote: str, thread_day: str) -> str | None:
    """Only an explicit calendar date written in the action quote ("2026-11-20", "Nov 20", "20 November"),
    resolved against the thread's date for the year; weekdays and "soon" stay unset."""
    iso = _ISO.search(quote or "")
    if iso:
        found = parse_day(iso.group(1))
        return found.isoformat() if found else None
    sent = parse_day(thread_day)
    if sent is None:
        return None
    day_month = _DAY_MONTH.search(quote or "")
    if day_month:
        return _dated(sent, int(day_month.group(1)), day_month.group(2))
    month_day = _MONTH_DAY.search(quote or "")
    if month_day:
        return _dated(sent, int(month_day.group(2)), month_day.group(1))
    return None


def _dated(sent: date, day: int, month_word: str) -> str | None:
    month = [m[:3] for m in _MONTHS].index(month_word[:3].lower()) + 1
    for year in (sent.year, sent.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            return None
        if candidate >= sent:
            return candidate.isoformat()
    return None


def whole_word_in(name: str, text: str) -> bool:
    name = name.strip()
    return bool(name) and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text or "", re.IGNORECASE) is not None


def labels_for(finding: Finding, labels: Sequence[Named]) -> tuple[Named, ...]:
    return tuple(label for label in labels if whole_word_in(label.name, finding.text))[:MAX_LABELS]


def project_for(finding: Finding, projects: Sequence[Named]) -> Named | None:
    return next((p for p in projects if whole_word_in(p.name, finding.text)), None)


def description(finding: Finding) -> str:
    """Assembled in code: where it came from, the verbatim decision, who was in it and the link. No model
    prose goes in."""
    where = f"From a chat thread in #{finding.channel}" if finding.channel else "From a chat thread"
    when = f" on {finding.date}" if finding.date else ""
    quote = " ".join(finding.action_quote.split())
    people = ", ".join(finding.participants) or "unknown"
    link = finding.link or ("the pasted thread" if finding.pasted else
                            f"channel {finding.channel_id or 'unknown'}, thread {finding.thread_ts or 'unknown'}")
    return f"{where}{when}.\n\n> {quote}\n\nParticipants: {people}\nThread: {link}"


def build(finding: Finding, team: Named | None, projects: Sequence[Named], labels: Sequence[Named]) -> IssueDraft:
    return IssueDraft(finding_id=finding.id, title=finding.title, description=description(finding), team=team,
                      project=project_for(finding, projects), labels=labels_for(finding, labels), priority=None,
                      due=finding.due)


def first_words(text: str) -> str:
    return " ".join(text.split()[:DUP_WORDS])


def jaccard(a: str, b: str) -> float:
    left, right = set(_WORD.findall(a.casefold())), set(_WORD.findall(b.casefold()))
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def ordered(findings: Sequence[Finding]) -> tuple[Finding, ...]:
    """Bugs first, then the most recent thread first."""
    def key(f: Finding) -> tuple:
        try:
            last = -float(f.last_ts)
        except ValueError:
            day = parse_day(f.date)
            last = -float(day.toordinal()) if day else 0.0
        return (f.kind != "bug", last, f.id)

    return tuple(sorted(findings, key=key))
