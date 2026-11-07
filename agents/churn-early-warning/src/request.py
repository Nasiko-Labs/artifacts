"""Pure: what the user's message asks for, parsed in code (never by the model): the renewal lookahead,
the change lookback, a minimum MRR, the list numbers to draft for, an inline recipient address, the
accounts named by an envelope from an earlier step, and JSON output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from records import load_json_object, text_of

DEFAULT_LOOKAHEAD = 30
MIN_LOOKAHEAD, MAX_LOOKAHEAD = 7, 90
DEFAULT_LOOKBACK = 30
MIN_LOOKBACK, MAX_LOOKBACK = 7, 60

_WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10}
_NUM = r"(\d{1,3}|a|one|two|three|four|five|six|seven|eight|nine|ten)"
_SPAN = rf"{_NUM}\s*(days?|weeks?|months?)"
_AHEAD = re.compile(rf"\b(?:next|coming|upcoming|within|in)\s+(?:the\s+)?(?:next\s+)?{_SPAN}", re.IGNORECASE)
_BACK = re.compile(rf"\b(?:last|past|previous)\s+{_SPAN}", re.IGNORECASE)
_THIS_MONTH = re.compile(r"\bthis month\b", re.IGNORECASE)
_MIN_MRR = re.compile(r"\b(?:above|over|more than|greater than|at least|min(?:imum)?(?: mrr)?(?: of)?)\s*"
                      r"(?:[$€£]\s*)?(\d[\d,]*(?:\.\d+)?)\s*(k)?\b|>=?\s*(?:[$€£]\s*)?(\d[\d,]*(?:\.\d+)?)\s*(k)?",
                      re.IGNORECASE)
_DRAFT = re.compile(r"\b(?:draft|write|outreach|reach out|email|contact|send)\b", re.IGNORECASE)
_NUMBERED = re.compile(r"#\s*(\d{1,2})\b")
_TOP = re.compile(rf"\btop\s+{_NUM}\b|\btop\b", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    lookahead_days: int = DEFAULT_LOOKAHEAD
    lookback_days: int = DEFAULT_LOOKBACK
    min_mrr: Decimal | None = None          # major units of the account's currency
    draft_ids: tuple[str, ...] = ()
    email: str = ""                          # a recipient the user typed ("draft #3 to x@y.com")
    focus: tuple[str, ...] = ()              # account names from an envelope given as input
    explicit_window: bool = False


def _count(word: str) -> int:
    return _WORDS.get(word.lower()) or int(word)


def _days(match: re.Match) -> int:
    n, unit = _count(match.group(1)), match.group(2).lower()
    return n * (7 if unit.startswith("week") else 30 if unit.startswith("month") else 1)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def min_mrr(text: str) -> Decimal | None:
    match = _MIN_MRR.search(text or "")
    if not match:
        return None
    number, k = (match.group(1), match.group(2)) if match.group(1) else (match.group(3), match.group(4))
    try:
        value = Decimal(number.replace(",", ""))
    except InvalidOperation:
        return None
    return value * 1000 if k else value


def draft_ids(text: str) -> tuple[str, ...]:
    """`draft outreach for #1 and #3` → ("1", "3"); `draft for the top two` → ("1", "2")."""
    if not _DRAFT.search(text or ""):
        return ()
    numbered = tuple(dict.fromkeys(str(int(n)) for n in _NUMBERED.findall(text)))
    if numbered:
        return numbered
    top = _TOP.search(text)
    if top is None:
        return ()
    count = _count(top.group(1)) if top.group(1) else 1
    return tuple(str(n) for n in range(1, min(count, 10) + 1))


def focus_accounts(text: str) -> tuple[str, ...]:
    """Account names from an envelope (`{"at_risk": [{"account": ...}]}`) given as input."""
    data = load_json_object(text or "")
    rows = data.get("at_risk") if data else None
    if not isinstance(rows, list):
        return ()
    names = (text_of(r.get("account")) for r in rows if isinstance(r, dict))
    return tuple(dict.fromkeys(n for n in names if n))


def wants_json(text: str) -> bool:
    return bool(_JSON_WORDS.search(text or ""))


def parse_request(query: str) -> Request:
    text = query or ""
    ahead, back = _AHEAD.search(text), _BACK.search(text)
    lookahead = _clamp(_days(ahead), MIN_LOOKAHEAD, MAX_LOOKAHEAD) if ahead else DEFAULT_LOOKAHEAD
    if not ahead and _THIS_MONTH.search(text):
        lookahead = DEFAULT_LOOKAHEAD
    lookback = _clamp(_days(back), MIN_LOOKBACK, MAX_LOOKBACK) if back else DEFAULT_LOOKBACK
    email = _EMAIL.search(text)
    return Request(lookahead_days=lookahead, lookback_days=lookback, min_mrr=min_mrr(text),
                   draft_ids=draft_ids(text), email=email.group(0) if email else "", focus=focus_accounts(text),
                   explicit_window=bool(ahead or back))
