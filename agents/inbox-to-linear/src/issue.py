"""Pure: everything about a proposed issue that code decides. The title's shape, the priority and due date
from the email's own words, labels and project only when they exist in the tracker and appear in the
email, the description template, the duplicate test and the order of proposals."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, timedelta

from models import IssueDraft, Named, Request
from textcheck import parse_day

MAX_TITLE_CHARS = 80
MAX_LABELS = 3
DUPLICATE_JACCARD = 0.7

TITLE_VERBS = frozenset({
    "add", "allow", "analyze", "analyse", "audit", "build", "change", "check", "clean", "configure", "create",
    "debug", "define", "delete", "deliver", "deploy", "design", "document", "draft", "enable", "evaluate",
    "export", "extend", "fix", "handle", "implement", "import", "improve", "increase", "investigate", "make",
    "migrate", "move", "optimize", "optimise", "prepare", "provide", "reduce", "refactor", "remove", "rename",
    "replace", "resolve", "restore", "review", "rewrite", "schedule", "send", "set", "share", "ship", "show",
    "speed", "support", "test", "track", "update", "upgrade", "validate", "verify", "write",
})
_TYPE_VERB = {"bug": "Fix", "feature": "Add", "task": "Complete", "investigation": "Investigate",
              "review": "Review"}

URGENT = "urgent"
LOW = "low"
_LOW_WORDS = re.compile(r"\bnot urgent\b|\bno rush\b|\blow priority\b|\bwhen(?:ever)? you (?:can|get a chance)\b",
                        re.IGNORECASE)
_URGENT_WORDS = re.compile(r"\burgent\b|\basap\b|\bas soon as possible\b", re.IGNORECASE)

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_WEEKDAY = re.compile(r"\b(" + "|".join(_WEEKDAYS) + r")\b", re.IGNORECASE)
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


def fix_title(title: str, ask_type: str) -> str:
    """The code fallback after one repair: a leading verb from the ask type, no trailing period, at most
    80 characters cut at a word boundary."""
    text = " ".join(title.split()).rstrip(". ")
    words = text.split()
    if not words or words[0].casefold().strip(":,") not in TITLE_VERBS:
        verb = _TYPE_VERB.get(ask_type, "Handle")
        first = words[0] if words else ""
        lowered = first[:1].lower() + first[1:] if first[1:2].islower() else first
        text = f"{verb} {' '.join([lowered, *words[1:]])}" if words else f"{verb} the request"
    if len(text) <= MAX_TITLE_CHARS:
        return text
    cut = text[:MAX_TITLE_CHARS + 1].rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-.")[:MAX_TITLE_CHARS]


def priority_from(text: str) -> str | None:
    """Only explicit words set a priority: "urgent"/"ASAP" -> urgent, "when you can"/"low priority" -> low."""
    if _LOW_WORDS.search(text or ""):
        return LOW
    if _URGENT_WORDS.search(text or ""):
        return URGENT
    return None


def due_from(phrase: str | None, sent: date | None) -> str | None:
    """The deadline the email states, resolved against the email's date; anything unclear stays unset."""
    if not phrase or sent is None:
        return None
    text = phrase.casefold()
    if "today" in text or "end of day" in text or "eod" in text.split():
        return sent.isoformat()
    if "tomorrow" in text:
        return (sent + timedelta(days=1)).isoformat()
    if "end of the week" in text or "end of week" in text:
        return _next_weekday(sent, 4).isoformat()
    if "next week" in text:
        return None
    return _explicit(text, sent)


def _explicit(text: str, sent: date) -> str | None:
    iso = _ISO.search(text)
    if iso:
        found = parse_day(iso.group(1))
        return found.isoformat() if found else None
    weekday = _WEEKDAY.search(text)
    if weekday:
        return _next_weekday(sent, _WEEKDAYS.index(weekday.group(1).lower())).isoformat()
    day_month = _DAY_MONTH.search(text)
    month_day = _MONTH_DAY.search(text)
    if day_month:
        return _dated(sent, int(day_month.group(1)), day_month.group(2))
    if month_day:
        return _dated(sent, int(month_day.group(2)), month_day.group(1))
    return None


def _next_weekday(sent: date, weekday: int) -> date:
    return sent + timedelta(days=(weekday - sent.weekday()) % 7)


def _dated(sent: date, day: int, month_word: str) -> str | None:
    month = [m[:3] for m in _MONTHS].index(month_word[:3].lower()) + 1
    try:
        candidate = date(sent.year, month, day)
    except ValueError:
        return None
    if candidate < sent:
        try:
            candidate = date(sent.year + 1, month, day)
        except ValueError:
            return None
    return candidate.isoformat()


def whole_word_in(name: str, text: str) -> bool:
    name = name.strip()
    return bool(name) and re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", text or "", re.IGNORECASE) is not None


def labels_for(request: Request, labels: Sequence[Named]) -> tuple[Named, ...]:
    text = f"{request.subject}\n{request.text}"
    return tuple(label for label in labels if whole_word_in(label.name, text))[:MAX_LABELS]


def project_for(request: Request, projects: Sequence[Named]) -> Named | None:
    text = f"{request.subject}\n{request.text}"
    return next((p for p in projects if whole_word_in(p.name, text)), None)


def source_line(request: Request) -> str:
    if request.link:
        return request.link
    parts = [f'"{request.subject}"' if request.subject else "", request.date,
             f"message id {request.message_id}" if request.message_id else ""]
    return ", ".join(p for p in parts if p) or "the pasted email"


def description(request: Request) -> str:
    """Assembled in code: the requester, the verbatim ask and the source. No model prose goes in."""
    who = request.sender_name or "unknown"
    address = f" ({request.sender})" if request.sender else ""
    when = f" on {request.date}" if request.date else ""
    quote = " ".join(request.ask_quote.split())
    return f"Requested by {who}{address}{when}.\n\n> {quote}\n\nSource email: {source_line(request)}"


def build(request: Request, team: Named | None, projects: Sequence[Named], labels: Sequence[Named]) -> IssueDraft:
    return IssueDraft(request_id=request.id, title=request.title, description=description(request), team=team,
                      project=project_for(request, projects), labels=labels_for(request, labels),
                      priority=request.priority, due=request.due)


def jaccard(a: str, b: str) -> float:
    left, right = set(_WORD.findall(a.casefold())), set(_WORD.findall(b.casefold()))
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def ordered(requests: Sequence[Request]) -> tuple[Request, ...]:
    """Urgent first, then oldest email first."""
    def key(r: Request) -> tuple:
        sent = parse_day(r.date) or date.max
        return (r.priority != URGENT, sent, r.id)

    return tuple(sorted(requests, key=key))
