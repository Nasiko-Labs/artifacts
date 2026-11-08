"""Pure: what the user's message asks for, parsed in code. Target day(s) (default yesterday UTC; "last N days" up to
7, each checked against its own baseline; an ISO date or "March 14"), the baseline length (clamped 14-60), a
`#channel`, json mode, whether it asks to post, and an envelope from a previous step (its `anomaly.day`)."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import detect

MAX_DAYS = 7
_MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                           "nov", "dec"))}
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|" \
         r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_MONTH_DAY = re.compile(rf"\b{_MONTH}\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.IGNORECASE)
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}\b", re.IGNORECASE)
_LAST_N = re.compile(r"\b(?:last|past|previous)\s+(\d{1,2}|two|three|four|five|six|seven)\s+days?\b", re.IGNORECASE)
_WORD_NUMBERS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
_BASELINE = re.compile(r"\bbaseline\D{0,12}?(\d{1,3})\b|\b(\d{1,3})[- ]days?\s+baseline\b", re.IGNORECASE)
_TODAY = re.compile(r"\btoday\b", re.IGNORECASE)
_CHANNEL = re.compile(r"(?<![\w#&])#([A-Za-z0-9][\w.-]{0,79})")
_JSON_MODE = re.compile(r"\bas json\b|\bjson only\b", re.IGNORECASE)
_POST = re.compile(r"\b(?:post|send|share|publish)\b(?!\s+(?:mortem|card))", re.IGNORECASE)
_DOMAIN = re.compile(r"\b(?:revenue|mrr|arr|churn\w*|anomal\w*|expansion|contraction|failed|payments?|"
                     r"subscriptions?|customers?|spikes?|drops?|baseline|check|movement|money|sales|downgrades?|"
                     r"upgrades?|cancell?ations?|cancel\w*|yesterday|days?)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    days: tuple[date, ...]
    names_days: bool = False
    baseline_days: int = detect.DEFAULT_BASELINE_DAYS
    names_baseline: bool = False
    channel: str | None = None
    json_mode: bool = False
    wants_post: bool = False

    def to_dict(self) -> dict:
        return {"days": [d.isoformat() for d in self.days], "names_days": self.names_days,
                "baseline_days": self.baseline_days, "names_baseline": self.names_baseline, "channel": self.channel,
                "json_mode": self.json_mode, "wants_post": self.wants_post}

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Request:
        return Request(days=tuple(date.fromisoformat(str(d)) for d in data.get("days") or ()),
                       names_days=bool(data.get("names_days")),
                       baseline_days=int(data.get("baseline_days") or detect.DEFAULT_BASELINE_DAYS),
                       names_baseline=bool(data.get("names_baseline")), channel=data.get("channel") or None,
                       json_mode=bool(data.get("json_mode")), wants_post=bool(data.get("wants_post")))


def parse(text: str, today: date, metadata: Mapping[str, Any] | None = None) -> Request:
    meta = dict(metadata or {})
    query = text or ""
    days = _envelope_days(query) or _days(_without_rows(query), today)
    baseline = _baseline(query)
    channel = _CHANNEL.search(query)
    json_mode = bool(_JSON_MODE.search(query)) or str(meta.get("output") or "").casefold() == "json"
    return Request(days=days or (today - timedelta(days=1),), names_days=bool(days),
                   baseline_days=detect.clamp_baseline(baseline), names_baseline=baseline is not None,
                   channel=channel.group(1) if channel else None, json_mode=json_mode,
                   wants_post=bool(_POST.search(query)))


def mentions_domain(text: str) -> bool:
    return bool(_DOMAIN.search(text or ""))


def _without_rows(text: str) -> str:
    """The message minus pasted daily rows, so a row's date is never read as the day to check."""
    return "\n".join(line for line in text.splitlines() if not re.match(r"^\s*\d{4}-\d{2}-\d{2}\s*[,\t;]", line))


def _days(text: str, today: date) -> tuple[date, ...]:
    last_n = _LAST_N.search(text)
    if last_n:
        raw = last_n.group(1).casefold()
        n = max(1, min(MAX_DAYS, _WORD_NUMBERS.get(raw) or int(raw)))
        return tuple(today - timedelta(days=i) for i in range(n, 0, -1))
    found = [d for d in (*_iso_days(text), *_named_days(text, today)) if d <= today]
    if found:
        return tuple(sorted(set(found)))[-MAX_DAYS:]
    if _TODAY.search(text):
        return (today,)
    return ()


def _iso_days(text: str) -> list[date]:
    out = []
    for raw in _ISO.findall(text):
        try:
            out.append(date.fromisoformat(raw))
        except ValueError:
            continue
    return out


def _named_days(text: str, today: date) -> list[date]:
    pairs = [(m.group(1), m.group(2)) for m in _MONTH_DAY.finditer(text)]
    pairs += [(m.group(2), m.group(1)) for m in _DAY_MONTH.finditer(text)]
    out = []
    for month_word, day_word in pairs:
        month = _MONTHS[month_word[:3].casefold()]
        try:
            found = date(today.year, month, int(day_word))
        except ValueError:
            continue
        out.append(found if found <= today else date(today.year - 1, month, int(day_word)))
    return out


def _baseline(text: str) -> int | None:
    match = _BASELINE.search(text)
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def _envelope_days(text: str) -> tuple[date, ...]:
    """Days named by a previous step's envelope (`anomaly` or `anomalies[]`, each with a `day`)."""
    body = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    if not body.startswith("{"):
        return ()
    try:
        data = json.loads(body)
    except ValueError:
        return ()
    if not isinstance(data, dict):
        return ()
    entries = [data.get("anomaly"), *(data.get("anomalies") or [])]
    days = set()
    for entry in entries:
        if isinstance(entry, Mapping):
            try:
                days.add(date.fromisoformat(str(entry.get("day"))))
            except ValueError:
                continue
    return tuple(sorted(days))[-MAX_DAYS:]
