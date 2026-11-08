"""Pure: hosts, URLs, ARNs, namespaces and numbers the model wrote that are not in the user's text become
visible angle-bracket placeholders; placeholders are listed and can be filled in later with the user's value."""

from __future__ import annotations

import re
from collections.abc import Iterable

HOST, URL, DASHBOARD_URL, ARN, NAMESPACE = "<HOST>", "<URL>", "<DASHBOARD URL>", "<ARN>", "<NAMESPACE>"
TIME, THRESHOLD, REPLICAS = "<time: fill in>", "<threshold: fill in>", "<replicas: fill in>"
MAX_FILL_CHARS = 200

_URL = re.compile(r"\bhttps?://[^\s`'\"<>)\]]*[^\s`'\"<>)\],.;:!?]", re.IGNORECASE)
_ARN = re.compile(r"\barn:aws[\w-]*:[^\s`'\"<>]+", re.IGNORECASE)
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b")
_HOSTNAME = re.compile(r"\b(?:[a-z0-9-]+\.)+(?:internal|local|corp|lan|com|net|io|org|cloud|dev|svc)\b(?::\d+)?",
                       re.IGNORECASE)
_NAMESPACE_FLAG = re.compile(r"(\s(?:-n|--namespace)[= ]\s*)([a-z0-9][a-z0-9-]*)", re.IGNORECASE)
_TIME = re.compile(r"\b\d+(?:\.\d+)?\s*(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|ms|days?)\b|\b\d+[smh]\b",
                   re.IGNORECASE)
_PERCENT = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|percent\b)", re.IGNORECASE)
_REPLICA_FLAG = re.compile(r"(--replicas[= ]\s*)(\d+)\b", re.IGNORECASE)
PLACEHOLDER = re.compile(r"<(?:[A-Z][A-Z0-9 _/-]{1,40}\??|[a-z][a-z -]{0,30}(?::\s*fill in|\?)|previous [a-z ]{1,30})>")
_DASHBOARDISH = re.compile(r"dashboard|grafana|kibana|datadog|/d/", re.IGNORECASE)
_SPACES = re.compile(r"\s+")


def normalise(text: str) -> str:
    return _SPACES.sub(" ", (text or "").casefold()).strip()


class Grounder:
    """Rewrites model text against one source. `source` is the user's text (plus their filled-in values)."""

    def __init__(self, source: str):
        self._source = normalise(source)
        self.replaced = 0

    def _known(self, value: str) -> bool:
        """`value` appears in the source as a whole token run ("5 minutes" is not in "15 minutes")."""
        pattern = rf"(?<![\w.]){re.escape(normalise(value))}(?!\w)"
        return re.search(pattern, self._source) is not None

    def ground(self, text: str | None) -> str:
        if not text:
            return text or ""
        out = _URL.sub(lambda m: self._swap(m, DASHBOARD_URL if _DASHBOARDISH.search(m.group(0)) else URL), text)
        out = _ARN.sub(lambda m: self._swap(m, ARN), out)
        out = _IPV4.sub(lambda m: self._swap(m, HOST), out)
        out = _HOSTNAME.sub(lambda m: self._swap(m, HOST), out)
        out = _NAMESPACE_FLAG.sub(lambda m: self._swap_group(m, NAMESPACE), out)
        out = _REPLICA_FLAG.sub(lambda m: self._swap_group(m, REPLICAS), out)
        out = _TIME.sub(lambda m: self._swap(m, TIME), out)
        return _PERCENT.sub(lambda m: self._swap(m, THRESHOLD), out)

    def _swap(self, match: re.Match, placeholder: str) -> str:
        if self._known(match.group(0)):
            return match.group(0)
        self.replaced += 1
        return placeholder

    def _swap_group(self, match: re.Match, placeholder: str) -> str:
        if self._known(match.group(2)):
            return match.group(0)
        self.replaced += 1
        return match.group(1) + placeholder


def found_in(texts: Iterable[str]) -> list[str]:
    """Distinct placeholders, in first-seen order."""
    seen: list[str] = []
    for text in texts:
        for name in PLACEHOLDER.findall(text or ""):
            if name not in seen:
                seen.append(name)
    return seen


def clean_value(value: str) -> str:
    """A user's fill-in value: one line, no backticks or angle brackets, bounded."""
    flat = _SPACES.sub(" ", value or "").strip().strip("`'\"")
    return re.sub(r"[`<>]", "", flat)[:MAX_FILL_CHARS]


def fill(text: str | None, name: str, value: str) -> str:
    """Replaces every `<name>` (case-insensitive, tolerant of the brackets being omitted) with `value`."""
    if not text:
        return text or ""
    inner = name.strip().strip("<>").strip()
    return re.sub(rf"<\s*{re.escape(inner)}\s*>", lambda _m: value, text, flags=re.IGNORECASE)
