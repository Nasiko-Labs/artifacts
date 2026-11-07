"""Pure: is a success-metric line measurable? It needs a quantified target (a number with a unit or
comparator), a time window or date, and ideally a baseline. Code decides; the model never does."""

from __future__ import annotations

import re
from dataclasses import dataclass

MISSING_NUMBER_POINTS = 8
MISSING_WINDOW_POINTS = 4
MISSING_BASELINE_POINTS = 3

_MONTHS = ("january|february|march|april|june|july|august|september|october|november|december|"
           "jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec")
_NUMBER = re.compile(r"\d")
_UNIT = re.compile(r"%|\bper\b|\bms\b|\d\s*(?:ms|s|x|k|m)\b|\b(?:seconds?|secs?|minutes?|mins?|hours?|days?|weeks?|"
                   r"months?|users?|customers?|accounts?|tickets?|requests?|orders?|sessions?|signups?|sign-ups?|"
                   r"points?|nps|csat|dollars?|usd|eur|rows?|records?|errors?|times)\b|[$€£<>≤≥]|\bat least\b|"
                   r"\bat most\b|\bunder\b|\bbelow\b|\babove\b|\bover\b|\bless than\b|\bmore than\b|\bfewer than\b|"
                   r"\bup to\b|\bfrom\b.+\bto\b", re.IGNORECASE)
_WINDOW = re.compile(rf"\bwithin\b|\bby (?:the )?(?:end|launch|ga|q[1-4]|{_MONTHS}|\d{{4}}|week|month|day)|"
                     r"\bper (?:day|week|month|quarter|year)\b|\b(?:daily|weekly|monthly|quarterly|annually|yearly)\b|"
                     rf"\bq[1-4]\b|\b(?:{_MONTHS})\b|\bmay \d|\b\d+\s*(?:days?|weeks?|months?|quarters?|years?)\b|"
                     r"\bafter (?:launch|release|ga)\b|\b(?:first|next|last) (?:\w+ )?(?:days?|weeks?|months?|"
                     r"quarters?|year)\b|\b20\d\d\b|\bh[12]\b|\bfy\d", re.IGNORECASE)
_BASELINE = re.compile(r"\bfrom\b|\bbaseline\b|\bcurrently\b|\btoday\b|\bcurrent\b|\bvs\.?\b|\bversus\b|"
                       r"\bcompared to\b", re.IGNORECASE)
_MARKER = re.compile(r"^\s*(?:[-*•+]|\d{1,2}[.)])\s+")


@dataclass(frozen=True)
class MetricCheck:
    line: int
    text: str
    has_target: bool
    has_window: bool
    has_baseline: bool

    @property
    def measurable(self) -> bool:
        return self.has_target and self.has_window

    @property
    def points(self) -> int:
        return ((0 if self.has_target else MISSING_NUMBER_POINTS) + (0 if self.has_window else MISSING_WINDOW_POINTS)
                + (0 if self.has_baseline else MISSING_BASELINE_POINTS))


def clean(line: str) -> str:
    return _MARKER.sub("", line or "").strip().strip("|").strip()


def check(n: int, line: str) -> MetricCheck:
    text = clean(line)
    target = bool(_NUMBER.search(text)) and bool(_UNIT.search(text))
    return MetricCheck(n, text, target, bool(_WINDOW.search(text)), bool(_BASELINE.search(text)))


def metric_lines(body: list[tuple[int, str]]) -> list[MetricCheck]:
    """One check per metric line of the Metrics section (sub-headings and table separators skipped)."""
    out = []
    for n, line in body:
        text = clean(line)
        if len(text.split()) < 2 or re.fullmatch(r"[-:| ]+", text) or line.lstrip().startswith("#"):
            continue
        out.append(check(n, line))
    return out
