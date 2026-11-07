"""Pure: the deterministic triage rules, first match wins. A thread no rule decides goes to the model.

1. the last message is the user's            -> excluded (counted as "already answered")
2. bulk/automated sender with a security or billing failure phrase -> urgent (quoted)
3. bulk sender, not a VIP                    -> ignore ("bulk sender: <sender>, <why>")
4. alert-like subject (receipt, shipping ...), no ask, not a VIP -> ignore
5. deadline today or earlier, sent to the user directly or by a VIP -> urgent (quoted)
6. a VIP asks the user something             -> urgent (quoted)
Rule 2 runs before the bulk rule because no-reply senders are both bulk and automated (plan rules 2-3).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from models import Bucket, Mail, Thread
from signals import Signals

_NAME = re.compile(r'^\s*"?([^"<]+?)"?\s*<')


@dataclass(frozen=True)
class RuleOutcome:
    excluded: bool = False
    thread: Thread | None = None


AMBIGUOUS = RuleOutcome()


def display_name(sender: str) -> str:
    match = _NAME.match(sender or "")
    if match and match.group(1).strip():
        return match.group(1).strip()
    return (sender or "unknown").strip().strip("<>") or "unknown"


def make_thread(mail: Mail, signals: Signals, bucket: Bucket, reason: str, evidence: str | None,
                source: str) -> Thread:
    return Thread(id=mail.id, sender=display_name(mail.sender), subject=mail.subject or "(no subject)",
                  date=mail.date, bucket=bucket, reason=reason, evidence=evidence,
                  deadline=signals.deadline.date.isoformat() if signals.deadline else None,
                  age_days=None if signals.age_hours is None else round(signals.age_hours / 24, 1),
                  link=mail.link, source=source, vip=signals.from_vip, bulk=signals.is_bulk)


def apply(mail: Mail, signals: Signals, today: date) -> RuleOutcome:
    if mail.last_from_me:
        return RuleOutcome(excluded=True)
    name = display_name(mail.sender)
    if (signals.is_bulk or signals.alert_like) and signals.failure_quote:
        return _decided(mail, signals, "urgent", "security or billing failure", signals.failure_quote)
    if signals.is_bulk and not signals.from_vip:
        return _decided(mail, signals, "ignore", f"bulk sender: {name}, {signals.bulk_why}", None)
    if signals.alert_like and not signals.from_vip and not signals.question_quote:
        return _decided(mail, signals, "ignore", f"automated notice: {name}", None)
    deadline = signals.deadline
    if deadline and deadline.date <= today and (signals.direct_to_me or signals.from_vip):
        when = "today" if deadline.date == today else f"overdue since {deadline.date.isoformat()}"
        return _decided(mail, signals, "urgent", f"deadline {when} ({deadline.phrase})", deadline.sentence)
    if signals.from_vip and signals.question_quote:
        return _decided(mail, signals, "urgent", f"VIP {name} asks you", signals.question_quote)
    return AMBIGUOUS


def _decided(mail: Mail, signals: Signals, bucket: Bucket, reason: str, evidence: str | None) -> RuleOutcome:
    return RuleOutcome(thread=make_thread(mail, signals, bucket, reason, evidence, "rule"))
