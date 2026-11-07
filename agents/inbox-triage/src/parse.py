"""Pure: the request, parsed in code (window, sender filter, unread-only, VIPs, json mode, an envelope as
input) and the search query string built from it. No model call decides any of this."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, timedelta

DEFAULT_WINDOW_DAYS = 2
MIN_WINDOW_DAYS = 1
MAX_WINDOW_DAYS = 14
MAX_VIPS = 10
MAX_VIP_WORDS = 4

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEKDAY_RE = "|".join(WEEKDAYS)
_SINCE_WEEKDAY = re.compile(rf"\bsince (?:last )?({_WEEKDAY_RE})\b", re.IGNORECASE)
_LAST_N_DAYS = re.compile(r"\b(?:last|past|previous)\s+(\d{1,3})\s+days?\b|\b(\d{1,3})\s+days?\b", re.IGNORECASE)
_LAST_WEEK = re.compile(r"\b(?:last|past) week\b", re.IGNORECASE)
_THIS_WEEK = re.compile(r"\bthis week\b", re.IGNORECASE)
_ONE_DAY = re.compile(r"\b(?:since yesterday|today|last 24 hours|past 24 hours)\b", re.IGNORECASE)
_ONLY_UNREAD = re.compile(r"\b(?:only|just) (?:the )?unread\b|\bunread only\b", re.IGNORECASE)
_INCLUDE_READ = re.compile(r"\binclud(?:e|ing) read\b", re.IGNORECASE)
_SENDER = re.compile(r"\b(?:anything|mail|email|emails|messages?|something) from ([A-Z][\w.'-]+(?: [A-Z][\w.'-]+)?|"
                     r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+)")
_CONTINUE = re.compile(r"^\s*(?:continue|more|next|older(?: ones)?|keep going|show more|the rest|next slice)\b",
                       re.IGNORECASE)
_AS_JSON = re.compile(r"\b(?:as|in) json\b|\bjson only\b|\bjson output\b|^\s*json\s*$", re.IGNORECASE)
_SENTENCE = re.compile(r"[.;!?\n]+")
_VIP_STATEMENT = re.compile(
    r"^(?:also |note |remember |and )?(?:that )?(?P<names>[\w.'@&, -]+?) (?:is|are) (?:a |an |my |our )?"
    r"(?:vips?|urgent senders?|urgent|important senders?|priorit(?:y|ies))\b", re.IGNORECASE)
_VIP_ROLE = re.compile(r"\bmy (?:boss|manager|lead|ceo|vp|director) is (?P<names>[\w.'@ -]+)", re.IGNORECASE)
_VIP_LIST = re.compile(r"\bvips?(?: are| is|:) (?P<names>[\w.'@&, -]+)", re.IGNORECASE)
_NAME_SPLIT = re.compile(r",|\band\b|&", re.IGNORECASE)
_NOT_NAMES = frozenset({"what", "what's", "whats", "which", "who", "anything", "everything", "nothing", "something",
                        "it", "this", "that", "mail", "email", "what is", "nobody", "none", "all", "which ones"})
_NAME_FILLER = frozenset({"the", "my", "our"})
_SAFE_SENDER = re.compile(r"[^\w@.+'-]")


@dataclass(frozen=True)
class Request:
    window_days: int = DEFAULT_WINDOW_DAYS
    window_label: str = ""
    sender: str | None = None
    only_unread: bool = False
    include_read: bool = False
    is_continue: bool = False
    vips: tuple[str, ...] = ()
    wants_json: bool = False
    from_envelope: bool = False
    window_given: bool = False


def clamp_window(days: int) -> int:
    return max(MIN_WINDOW_DAYS, min(MAX_WINDOW_DAYS, int(days)))


def parse_request(text: str, today: date, metadata_json: bool = False) -> Request:
    envelope = _envelope(text)
    if envelope is not None:
        return _from_envelope(envelope, metadata_json)
    days, label, given = _window(text, today)
    return Request(window_days=days, window_label=label, sender=_sender(text),
                   only_unread=bool(_ONLY_UNREAD.search(text)), include_read=bool(_INCLUDE_READ.search(text)),
                   is_continue=bool(_CONTINUE.match(text)), vips=vips_from(text),
                   wants_json=metadata_json or wants_json(text), window_given=given)


def wants_json(text: str) -> bool:
    return bool(_AS_JSON.search(text or ""))


def _envelope(text: str) -> dict | None:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").removeprefix("json").strip()
    if not stripped.startswith("{"):
        return None
    try:
        data = json.loads(stripped)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _from_envelope(data: dict, metadata_json: bool) -> Request:
    """An envelope from a previous step: reads `window_days` and `vips`; everything else is ignored."""
    raw_days = data.get("window_days")
    days = clamp_window(raw_days) if isinstance(raw_days, int) and not isinstance(raw_days, bool) else DEFAULT_WINDOW_DAYS
    raw_vips = data.get("vips")
    vips = tuple(_clean_name(str(v)) for v in raw_vips if str(v).strip()) if isinstance(raw_vips, list) else ()
    output_json = str(data.get("output") or "").casefold() == "json"
    return Request(window_days=days, window_label=f"last {days} days", vips=tuple(v for v in vips if v)[:MAX_VIPS],
                   wants_json=metadata_json or output_json, from_envelope=True, window_given="window_days" in data)


def _window(text: str, today: date) -> tuple[int, str, bool]:
    since = _SINCE_WEEKDAY.search(text)
    if since:
        target = WEEKDAYS.index(since.group(1).casefold())
        back = (today.weekday() - target) % 7
        days = clamp_window(back or 1)
        return days, f"since {since.group(1)[:3].title()}", True
    if _THIS_WEEK.search(text):
        return clamp_window(today.weekday() or 1), "this week", True
    if _LAST_WEEK.search(text):
        return 7, "last 7 days", True
    match = _LAST_N_DAYS.search(text)
    if match:
        days = clamp_window(int(match.group(1) or match.group(2)))
        return days, f"last {days} day{'s' if days > 1 else ''}", True
    if _ONE_DAY.search(text):
        return 1, "last 1 day", True
    return DEFAULT_WINDOW_DAYS, f"last {DEFAULT_WINDOW_DAYS} days", False


def _sender(text: str) -> str | None:
    match = _SENDER.search(text or "")
    if not match:
        return None
    name = match.group(1).strip()
    return None if name.casefold() in WEEKDAYS else name


def vips_from(text: str) -> tuple[str, ...]:
    """VIP names or addresses the user stated in this message ("Dana is a VIP", "my boss is Dana Roy",
    "Dana and the billing team are urgent senders")."""
    found: list[str] = []
    for sentence in _SENTENCE.split(text or ""):
        sentence = sentence.strip()
        for pattern in (_VIP_STATEMENT, _VIP_ROLE, _VIP_LIST):
            match = pattern.search(sentence) if pattern is not _VIP_STATEMENT else pattern.match(sentence)
            if match:
                found.extend(_names(match.group("names")))
    unique = tuple(dict.fromkeys(n for n in found if n))
    return unique[:MAX_VIPS]


def _names(raw: str) -> list[str]:
    return [_clean_name(part) for part in _NAME_SPLIT.split(raw)]


def _clean_name(raw: str) -> str:
    words = [w for w in raw.strip(" ,.'\"").split() if w.casefold() not in _NAME_FILLER]
    name = " ".join(words[:MAX_VIP_WORDS])
    return "" if not name or name.casefold() in _NOT_NAMES else name


def search_query(request: Request, today: date) -> str:
    """The email search string, built in code from the window and filters."""
    since = today - timedelta(days=request.window_days)
    parts = ["in:inbox", f"after:{since.strftime('%Y/%m/%d')}"]
    if request.only_unread:
        parts.append("is:unread")
    if request.sender:
        safe = _SAFE_SENDER.sub("", request.sender.split()[0])
        if safe:
            parts.append(f"from:{safe}")
    return " ".join(parts)
