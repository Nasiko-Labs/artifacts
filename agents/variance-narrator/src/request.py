"""Pure: the options a message sets, read in code. Length (short, board, default), threshold ("flag over 5%"),
period label ("Q3 2026"), a direction filter ("only unfavourable") and JSON output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

SHORT, BOARD, DEFAULT = "short", "board", "default"
MAX_THRESHOLD = Decimal(100)
_SHORT = re.compile(r"\b(?:shorter|short(?:er)? version|short|brief(?:er)?|one[- ]sentence|two[- ]sentences?|"
                    r"two[- ]sentence|[12] sentences?|tl ?;? ?dr)\b", re.IGNORECASE)
_BOARD = re.compile(r"\bboard\b", re.IGNORECASE)
_DEFAULT = re.compile(r"\b(?:full|default|longer|normal|standard) version\b|\blonger\b", re.IGNORECASE)
_THRESHOLD = re.compile(r"\b(?:threshold|flag|materiality|over|above)\b(?: (?:of|to|at|is|lines|anything|items|"
                        r"variances|over|above))*\s*(\d{1,3}(?:\.\d+)?)\s*%", re.IGNORECASE)
_UNFAV = re.compile(r"\bunfavou?rable\b", re.IGNORECASE)
_FAV = re.compile(r"\b(?<!un)favou?rable\b", re.IGNORECASE)
_ONLY = re.compile(r"\bonly\b|\bjust\b", re.IGNORECASE)
_ALL = re.compile(r"\ball lines\b|\bshow all\b|\bevery line\b|\ball variances\b", re.IGNORECASE)
_JSON = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_PERIOD = re.compile(rf"\b(?:Q[1-4][ -]?(?:FY)?[ -]?'?\d{{2,4}}|FY ?'?\d{{2,4}}(?: ?Q[1-4])?|H[12] ?'?\d{{2,4}}|"
                     rf"{_MONTHS} '?\d{{2,4}}|(?:19|20)\d{{2}})\b", re.IGNORECASE)


@dataclass(frozen=True)
class Settings:
    length: str | None = None
    threshold: Decimal | None = None
    period: str = ""
    only: str | None = None        # "U" | "F" | "" (all) | None (unchanged)
    wants_json: bool = False


def length_of(text: str) -> str | None:
    if _BOARD.search(text or ""):
        return BOARD
    if _SHORT.search(text or ""):
        return SHORT
    return DEFAULT if _DEFAULT.search(text or "") else None


def threshold_of(text: str) -> Decimal | None:
    match = _THRESHOLD.search(text or "")
    if not match:
        return None
    value = Decimal(match.group(1))
    return value if 0 < value <= MAX_THRESHOLD else None


def only_of(text: str) -> str | None:
    if _ALL.search(text or ""):
        return ""
    if not _ONLY.search(text or ""):
        return None
    if _UNFAV.search(text or ""):
        return "U"
    return "F" if _FAV.search(text or "") else None


def period_of(text: str) -> str:
    match = _PERIOD.search(text or "")
    return " ".join(match.group(0).split()) if match else ""


def settings_of(text: str) -> Settings:
    return Settings(length_of(text), threshold_of(text), period_of(text), only_of(text), bool(_JSON.search(text or "")))
