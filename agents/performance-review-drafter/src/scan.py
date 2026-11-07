"""Pure: personal characteristics and health in the evidence, found before any model call. A flagged item is
left out of everything the model sees and reported by id ("left out: e4, e9"), never silently dropped.

A leave of absence is never penalised: when a review period is set and the evidence mentions leave, a neutral
note says the evidence covers only active time."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import replace

from evidence import Evidence

PERSONAL = "personal characteristics"

_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("age", (r"\d{2}[- ]years?[- ]old|years old|(?:his|her|their) age|age of \d{2}|retirement age|nearing retirement|"
            r"millennial|boomer|gen z")),
    ("pregnancy or parental leave", (r"pregnan\w*|maternity|paternity|parental leave|family leave|"
                                    r"new (?:baby|mom|mother|dad|father)|childcare")),
    ("marital status", r"married|divorc\w*|marital|spouse|husband|wife|wedding"),
    ("religion", (r"religio\w*|church|mosque|synagogue|temple|prayers?|ramadan|christian|muslim|jewish|hindu|"
                 r"sikh|buddhist")),
    ("race or ethnicity", r"ethnic\w*|racial\w*|skin colou?r|(?:his|her|their) race|race (?:and|or) ethnicity"),
    ("nationality or accent", (r"accent|nationality|immigra\w*|visa status|foreigner|native speaker|"
                              r"english is not (?:his|her|their) first")),
    ("disability", r"disab\w*|wheelchair|adhd|autis\w*|dyslex\w*|deaf|blind(?!\s+spots?)"),
    ("health", (r"sick leave|medical leave|illness|medical|surgery|hospital\w*|cancer|therapy|mental health|"
               r"depress(?:ed|ion)|anxiety|diagnos\w*|chronic")),
    ("sexual orientation", r"gay|lesbian|bisexual|sexual orientation|lgbt\w*|queer"),
    ("gender identity", r"transgender|gender identity|non-?binary"),
    ("union membership", r"trade union|labou?r union|union rep\w*|unioni[sz]\w*|union (?:meeting|activity|member)"),
)
_PATTERNS = tuple((name, re.compile(rf"\b(?:{rx})\b", re.IGNORECASE)) for name, rx in _CATEGORIES)
_LEAVE = re.compile(r"\b(?:leave of absence|on leave|parental leave|maternity leave|paternity leave|sick leave|"
                    r"medical leave|back from leave|returning from (?:\w+ )?leave|was out for \w+ (?:weeks|months))\b",
                    re.IGNORECASE)
LEAVE_NOTE = ("Evidence covers only active time: a leave of absence in the period is not counted for or against "
              "the person.")


def categories(text: str) -> list[str]:
    return [name for name, rx in _PATTERNS if rx.search(text or "")]


def scan(items: Sequence[Evidence]) -> tuple[list[Evidence], list[dict]]:
    """(items with flagged ones marked excluded, [{id, reason}] for the report)."""
    marked: list[Evidence] = []
    left_out: list[dict] = []
    for item in items:
        found = categories(item.text)
        if found and not item.excluded:
            marked.append(replace(item, excluded=PERSONAL))
            left_out.append({"id": item.id, "reason": f"{PERSONAL} ({', '.join(found)})"})
        else:
            marked.append(item)
    return marked, left_out


def mentions_leave(items: Sequence[Evidence]) -> bool:
    return any(_LEAVE.search(item.text) for item in items)
