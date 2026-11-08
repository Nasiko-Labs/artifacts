"""Pure: follow-up commands on the stored audit (no model call, except a title rewrite), and the diff of two
audits by check id."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

SEVERITY_ORDER = ("critical", "high", "medium", "low")
_ONLY = re.compile(r"\bonly (?:the )?(critical|high|medium|low)\b", re.IGNORECASE)
_EXPLAIN = re.compile(r"\b(?:explain|what(?:'s| is)|tell me about|more on|details? (?:on|of))\s+(?:finding\s+)?(f\d{1,2})\b",
                      re.IGNORECASE)
_BARE_ID = re.compile(r"^\s*(f\d{1,2})\s*\??\s*$", re.IGNORECASE)
_RETITLE = re.compile(r"\b(?:re-?write|redo|new|propose|suggest)\s+(?:the |a )?title\s+(?:for|around|targeting)\s+"
                      r"(?:the )?(?:keyword\s+)?[\"'“‘]?([^\"'”’\n?]{2,80})", re.IGNORECASE)
_ROBOTS = re.compile(r"\b(?:robots(?:\.txt)?|crawlers?|gptbot|claudebot|perplexitybot|ccbot)\b", re.IGNORECASE)
_GEO = re.compile(r"\b(?:ai[- ]search|ai[- ]ready|ai[- ]readiness|answer engines?|geo|aeo|llms?|chatgpt|"
                  r"perplexity|ai overviews?|ai engines?|cited by ai)\b", re.IGNORECASE)
_BREAKDOWN = re.compile(r"\b(?:score|breakdown|why|how (?:is|was) (?:it|this) (?:scored|calculated))\b",
                        re.IGNORECASE)
_JSON = re.compile(r"\b(?:as json|json only|in json|json output)\b", re.IGNORECASE)
_SHOW_AGAIN = re.compile(r"^(?:show|show it|again|the audit|full audit|show (?:the )?(?:audit|report|findings)|"
                         r"all findings)\s*[.!?]?$", re.IGNORECASE)
_AGAIN = re.compile(r"\b(?:again|re-?audit|re-?check|what changed|compare|new version|updated version|fixed)\b",
                    re.IGNORECASE)


@dataclass(frozen=True)
class Command:
    kind: str           # filter | explain | retitle | robots | geo | breakdown | json | show
    arg: str = ""


def parse_command(text: str) -> Command | None:
    t = (text or "").strip()
    if _JSON.search(t):
        return Command("json")
    match = _RETITLE.search(t)
    if match:
        return Command("retitle", " ".join(match.group(1).split()).strip(" .,"))
    match = _ONLY.search(t)
    if match:
        return Command("filter", match.group(1).casefold())
    match = _EXPLAIN.search(t) or _BARE_ID.match(t)
    if match:
        return Command("explain", match.group(1).upper())
    for kind, pattern in (("robots", _ROBOTS), ("geo", _GEO), ("breakdown", _BREAKDOWN), ("show", _SHOW_AGAIN)):
        if pattern.search(t):
            return Command(kind)
    return None


def severities_from(level: str) -> tuple[str, ...]:
    """"only high" means high and above."""
    return SEVERITY_ORDER[:SEVERITY_ORDER.index(level) + 1]


def asks_for_comparison(text: str) -> bool:
    return bool(_AGAIN.search(text or ""))


def _keys(items: Iterable[dict]) -> list[str]:
    keys = []
    for item in items:
        check = str(item.get("check") or item.get("id") or "")
        if not check or check == "Q" or item.get("severity") == "info":
            continue
        keys.append(check)
    return list(dict.fromkeys(keys))


def diff(previous: Iterable[dict], current: Iterable[dict]) -> dict[str, list[str]]:
    before, after = _keys(previous), _keys(current)
    return {"fixed": [k for k in before if k not in after], "new": [k for k in after if k not in before],
            "unchanged": [k for k in after if k in before]}
