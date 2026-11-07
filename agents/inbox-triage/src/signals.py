"""Pure: the signals every triage rule reads, extracted in code from what the tool returned for a thread.

Dates are resolved here (a deadline phrase is read relative to the day the message was sent and compared
with today); the model never computes one."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from models import Mail
from parse import WEEKDAYS

_EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
_AUTOMATED_LOCAL = re.compile(
    r"^(?:no-?reply|do-?not-?reply|donotreply|notifications?|notify|alerts?|mailer-daemon|receipts?|automated|"
    r"bounces?|news|newsletters?|digest|updates|marketing|postmaster)(?:[+._-].*)?$", re.IGNORECASE)
_BULK_LABELS = {"CATEGORY_PROMOTIONS": "promotions label", "CATEGORY_UPDATES": "updates label",
                "CATEGORY_FORUMS": "forums label", "CATEGORY_SOCIAL": "social label"}
_ALERT_SUBJECT = re.compile(
    r"\b(?:receipt|your order|order (?:confirmation|#?\d+)|shipped|shipping|delivered|delivery|invoice|statement|"
    r"security alert|sign-?in|verification code|password|subscription|newsletter|digest|webinar)\b", re.IGNORECASE)
FAILURE_PHRASES = re.compile(
    r"payment (?:has )?failed|failed payment|payment (?:was )?declined|card (?:was )?declined|"
    r"(?:could not|couldn't|unable to) process your payment|unusual sign-?in|suspicious sign-?in|"
    r"unusual activity|account (?:has been |was )?(?:suspended|locked)", re.IGNORECASE)
_REQUEST = re.compile(
    r"\b(?:can|could|would|will) you\b|\bplease\b|\blet me know\b|\bneed you to\b|\bneed your\b|"
    r"\b(?:approve|sign off|sign-off|review|confirm|send me|get back to me)\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_QUOTED_REPLY = re.compile(r"^\s*On .{3,200} wrote:\s*$", re.IGNORECASE | re.MULTILINE)
_NEGATED = re.compile(r"\b(?:not|no|isn't|nothing)\s+(?:\w+\s+)?$", re.IGNORECASE)

_SAME_DAY = re.compile(r"\b(today|tonight|eod|end of (?:the )?day|asap|as soon as possible|urgently|"
                       r"right away|immediately)\b", re.IGNORECASE)
# "urgent" alone counts only in a subject line ("URGENT: ..."): in a body it is too often about something else
# ("not urgent", "mark everything urgent").
_URGENT_WORD = re.compile(r"\burgent\b", re.IGNORECASE)
_TOMORROW = re.compile(r"\btomorrow\b", re.IGNORECASE)
_END_OF_WEEK = re.compile(r"\b(?:eow|end of (?:the )?week)\b", re.IGNORECASE)
_BY_WEEKDAY = re.compile(rf"\b(?:by|before|until|due|on) (?:this )?({'|'.join(WEEKDAYS)})\b", re.IGNORECASE)
_BY_ISO = re.compile(r"\b(?:by|before|until|due(?: on)?) (\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_BY_MONTH_DAY = re.compile(r"\b(?:by|before|until|due(?: on)?) (jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
                           r"[a-z]*\.? (\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE)
_YEAR_ROLLOVER_DAYS = 60


@dataclass(frozen=True)
class Deadline:
    date: date
    phrase: str
    sentence: str


@dataclass(frozen=True)
class Signals:
    sender_addr: str
    is_automated: bool
    bulk_why: str | None  # why the sender reads as bulk, or None
    alert_like: bool
    failure_quote: str | None
    direct_to_me: bool
    from_vip: bool
    question_quote: str | None
    deadline: Deadline | None
    age_hours: float | None

    @property
    def is_bulk(self) -> bool:
        return self.bulk_why is not None


def extract(mail: Mail, me: Sequence[str], vips: Sequence[str], now: datetime) -> Signals:
    sender_addr = address_of(mail.sender)
    automated = is_automated(sender_addr)
    sent = parse_iso(mail.date)
    own = own_text(mail.last_text)
    return Signals(
        sender_addr=sender_addr,
        is_automated=automated,
        bulk_why=_bulk_why(mail, automated),
        alert_like=bool(_ALERT_SUBJECT.search(mail.subject)),
        failure_quote=_first_sentence(FAILURE_PHRASES, (mail.subject, own)),
        direct_to_me=_direct(mail, me),
        from_vip=is_vip(mail.sender, vips),
        question_quote=question_in(own),
        deadline=_earliest(deadline_in(own, (sent or now).date()),
                           deadline_in(mail.subject, (sent or now).date(), subject=True)),
        age_hours=None if sent is None else round(max(0.0, (now - sent).total_seconds() / 3600), 1),
    )


def address_of(text: str) -> str:
    match = _EMAIL.search(text or "")
    return match.group(0).casefold() if match else ""


def addresses(text: str) -> tuple[str, ...]:
    return tuple(a.casefold() for a in _EMAIL.findall(text or ""))


def is_automated(addr: str) -> bool:
    return bool(addr) and bool(_AUTOMATED_LOCAL.match(addr.split("@", 1)[0]))


def is_vip(sender: str, vips: Sequence[str]) -> bool:
    """A VIP matches when every word of it appears in the sender (name or address), case-insensitively."""
    haystack = (sender or "").casefold()
    for vip in vips:
        words = [w for w in re.split(r"[\s]+", vip.casefold()) if w and w not in ("team", "the")]
        if words and all(w in haystack for w in words):
            return True
    return False


def own_text(text: str) -> str:
    """The newest message without the quoted history under it."""
    cut = _QUOTED_REPLY.search(text or "")
    body = text[:cut.start()] if cut else (text or "")
    return "\n".join(line for line in body.splitlines() if not line.lstrip().startswith(">"))


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(text or "") if s and s.strip()]


def question_in(text: str) -> str | None:
    """The first sentence that asks the reader something: ends with '?' or carries a request phrase."""
    for sentence in sentences(text):
        if sentence.endswith("?") or _REQUEST.search(sentence):
            return sentence
    return None


def deadline_in(text: str, sent_on: date, subject: bool = False) -> Deadline | None:
    """The earliest deadline stated in the text, resolved against the day the message was sent."""
    found: list[Deadline] = []
    for sentence in sentences(text):
        found.extend(_deadlines(sentence, sent_on, subject))
    return min(found, key=lambda d: d.date) if found else None


def _earliest(*deadlines: Deadline | None) -> Deadline | None:
    present = [d for d in deadlines if d is not None]
    return min(present, key=lambda d: d.date) if present else None


def _deadlines(sentence: str, sent_on: date, subject: bool) -> list[Deadline]:
    out: list[Deadline] = []
    if subject:
        out.extend(Deadline(sent_on, m.group(0), sentence) for m in _URGENT_WORD.finditer(sentence)
                   if not _NEGATED.search(sentence[:m.start()]))
    for match in _SAME_DAY.finditer(sentence):
        if not _NEGATED.search(sentence[:match.start()]):
            out.append(Deadline(sent_on, match.group(0), sentence))
    for match in _TOMORROW.finditer(sentence):
        out.append(Deadline(sent_on + timedelta(days=1), match.group(0), sentence))
    for match in _END_OF_WEEK.finditer(sentence):
        out.append(Deadline(sent_on + timedelta(days=(4 - sent_on.weekday()) % 7), match.group(0), sentence))
    for match in _BY_WEEKDAY.finditer(sentence):
        target = WEEKDAYS.index(match.group(1).casefold())
        out.append(Deadline(sent_on + timedelta(days=(target - sent_on.weekday()) % 7), match.group(0), sentence))
    for match in _BY_ISO.finditer(sentence):
        resolved = _iso_date(match.group(1))
        if resolved:
            out.append(Deadline(resolved, match.group(0), sentence))
    for match in _BY_MONTH_DAY.finditer(sentence):
        resolved = _month_day(match.group(1), int(match.group(2)), sent_on)
        if resolved:
            out.append(Deadline(resolved, match.group(0), sentence))
    return out


def _iso_date(text: str) -> date | None:
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _month_day(month: str, day: int, sent_on: date) -> date | None:
    try:
        resolved = date(sent_on.year, _MONTHS.index(month.casefold()[:3]) + 1, day)
    except ValueError:
        return None
    if (sent_on - resolved).days > _YEAR_ROLLOVER_DAYS:
        resolved = resolved.replace(year=resolved.year + 1)
    return resolved


def _bulk_why(mail: Mail, automated: bool) -> str | None:
    for label in mail.labels:
        if label.upper() in _BULK_LABELS:
            return _BULK_LABELS[label.upper()]
    if mail.list_unsubscribe:
        return "list-unsubscribe header"
    if mail.precedence_bulk:
        return "bulk precedence header"
    return "automated sender" if automated else None


def _direct(mail: Mail, me: Sequence[str]) -> bool:
    to = addresses(mail.to)
    if me:
        return any(addr.casefold() in to for addr in me)
    return len(to) == 1  # unknown "me": a single named recipient is the best available sign


def _first_sentence(pattern: re.Pattern, texts: Sequence[str]) -> str | None:
    for text in texts:
        for sentence in sentences(text):
            if pattern.search(sentence):
                return sentence
    return None


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
