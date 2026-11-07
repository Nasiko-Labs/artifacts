"""Pure (plus the regex subprocess): one entry point to the four analysers, and the code-side follow-ups
(new probes on the stored config; a suggested cron change, verified by the same parser)."""

from __future__ import annotations

import re
from datetime import datetime

import cron_check
import iam_check
import nginx_check
import regex_check
from analysis import Analysis
from cron import CronError, parse
from cron_phrase import phrase
from probes import Probes

_WEEKDAYS = re.compile(r"\b(?:weekdays?(?: only)?|mon(?:day)?\s*(?:-|to|through)\s*fri(?:day)?)\b", re.IGNORECASE)
_WEEKENDS = re.compile(r"\bweekends?(?: only)?\b", re.IGNORECASE)
_CHANGE = re.compile(r"\b(?:make it|change it|only on|run (?:it )?(?:on|only))\b", re.IGNORECASE)


def analyze(kind: str, config: str, probes: Probes, now: datetime) -> Analysis:
    if kind == "cron":
        return cron_check.analyze(config, probes, now)
    if kind == "regex":
        return regex_check.analyze(config, probes)
    if kind == "iam":
        return iam_check.analyze(config, probes)
    return nginx_check.analyze(config, probes)


def has_probe(kind: str, probes: Probes) -> bool:
    """Does this follow-up ask something code can answer on the stored config?"""
    if kind == "cron":
        return bool(probes.tz or probes.start or probes.count or cron_change(probes.words))
    if kind == "regex":
        return bool(probes.tests)
    if kind == "iam":
        return bool(probes.action)
    return bool(probes.urls)


def cron_change(words: str) -> str | None:
    """"make it run weekdays only" → "1-5"; "weekends" → "0,6"; else None."""
    if not _CHANGE.search(words or "") and not re.search(r"\bonly\b", words or "", re.IGNORECASE):
        return None
    if _WEEKDAYS.search(words or ""):
        return "1-5"
    if _WEEKENDS.search(words or ""):
        return "0,6"
    return None


def suggested_cron(config: str, dow: str) -> tuple[str, str] | None:
    """(new expression, its phrase) with day-of-month set to `*` and day-of-week to `dow`, or None."""
    try:
        spec = parse(config)
    except CronError:
        return None
    if spec.aws or spec.quartz or spec.reboot or len(spec.fields) != 5:
        return None
    minute, hour, _, month, _ = spec.fields
    prefix = f"CRON_TZ={spec.tz} " if spec.tz else ""
    expression = f"{prefix}{minute} {hour} * {month} {dow}"
    return expression, phrase(parse(expression))
