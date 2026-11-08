"""Pure: what the user asked, parsed in code: the sheet, tab, channel, json mode, dry run, and the follow-up
intents code can recognise without a model call (check again, post, change channel, reset the baseline, keep
watching on a schedule). Rule phrases are matched later against the sheet's headers (rules.py)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

_NAME = r"[A-Za-z0-9][\w&'./ -]{0,60}?"
_STOP_NAMES = frozenset({"my", "the", "our", "this", "that", "a", "it", "again", "sheet", "spreadsheet", "tracker",
                         "changes", "now", "for", "what", "everything"})
_SHEET_PATTERNS = (
    re.compile(r"[\"“']([^\"”']{1,80})[\"”']\s+(?:sheet|spreadsheet)\b", re.IGNORECASE),
    re.compile(r"\b(?:watch|check|monitor|track|read|open)\s+(?:the\s+|my\s+|our\s+)?[\"“']([^\"”']{1,80})[\"”']",
               re.IGNORECASE),
    re.compile(r"\b(?:sheet|spreadsheet)\s+(?:named|called|titled)\s+[\"“']?(" + _NAME + r")[\"”']?"
               r"(?=$|[,.;:!?]|\s+(?:and|then|tab|to|for)\b)", re.IGNORECASE),
    re.compile(r"\b(?:from|in|of|using|read|on|watch|check|monitor|track)\s+(?:the\s+|my\s+|our\s+)?(" + _NAME
               + r")\s+(?:sheet|spreadsheet)\b", re.IGNORECASE),
    # "watch the Project Tracker: ..." -- a capitalised name after a watch verb, up to a colon, comma or "for".
    re.compile(r"\b(?:[Ww]atch|[Cc]heck|[Mm]onitor|[Tt]rack|[Rr]ead|[Oo]pen)\s+(?:the\s+|my\s+|our\s+)?"
               r"([A-Z][\w&'./-]*(?:\s+[A-Z0-9][\w&'./-]*){0,5})(?=\s*(?:$|[:,;.!?]|\s+(?:for|and|to|tab)\b))"),
)
_TAB = re.compile(r"\b(?:tab|worksheet)\s+(?:named\s+|called\s+)?[\"“']?([\w &-]{1,40}?)[\"”']?(?=$|[,.;:!?]|\s+(?:and|"
                  r"then|to|for|from|of)\b)", re.IGNORECASE)
_CHANNEL = re.compile(r"(?<![\w&])#([A-Za-z0-9][\w.-]{0,79})")
_CHANNEL_WORDS = re.compile(r"\b(?:post|share|send)\b(?: it| this| that| the alert)? (?:to|in|into) (?:the )?"
                            r"([A-Za-z0-9][\w.-]{0,79}) channel\b", re.IGNORECASE)
_WANTS_POST = re.compile(r"\b(post|share|send)\b(?: (?:it|this|that|the alert|an alert|alert))?"
                         r"(?: (?:to|in|into)\b|$|[.!])|\b(?:alert|tell|notify) (?:the )?(?:team|channel)\b",
                         re.IGNORECASE)
_DRY_RUN = re.compile(r"\b(?:don'?t|do not|never|no need to)\s+post\b|\bdry[ -]?run\b|\bwithout posting\b",
                      re.IGNORECASE)
_JSON = re.compile(r"\b(?:as|in) json\b|\bjson only\b|\bjson output\b", re.IGNORECASE)
_CHECK = re.compile(r"\bwhat(?:'s| has| have)? changed\b|\bany(?:thing)? (?:new|changed?|changes|updates?)\b|"
                    r"\bsince (?:the )?last (?:time|check|run)\b|\bcheck (?:it |the sheet |again|now)|\brun (?:it )?again\b|"
                    r"\bre-?check\b|\bwhat'?s new\b|\bany updates\b", re.IGNORECASE)
_RESET = re.compile(r"\breset(?: the)? baseline\b|\bnew baseline\b|\bstart (?:over|fresh)\b|"
                    r"\bforget (?:the )?(?:last|previous) (?:check|run|snapshot)\b", re.IGNORECASE)
_CHANGE_CHANNEL = re.compile(r"\b(?:another|different|other) channel\b|\bchange (?:the )?channel\b", re.IGNORECASE)
_SCHEDULE = re.compile(r"\bovernight\b|\bevery (?:day|morning|hour|week|night|monday|few hours)\b|\bdaily\b|\bhourly\b|"
                       r"\bautomatically\b|\bin the background\b|\bkeep (?:watching|monitoring|an eye)\b|"
                       r"\bwhile i'?m (?:away|out|asleep)\b|\b(?:notify|ping|alert) me (?:when|whenever|if)\b|"
                       r"\bwhenever (?:it|something|anything) changes\b|\bon a schedule\b", re.IGNORECASE)
_DOMAIN = re.compile(r"\b(sheets?|spreadsheets?|tracker|tracking|rows?|columns?|status|due|deadline|overdue|blank|"
                     r"owner|baseline|changes?|changed|watch\w*|monitor\w*|alert|post|channel|table|snapshot)\b",
                     re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    sheet: str | None = None
    tab: str | None = None
    channel: str | None = None
    json_mode: bool = False
    dry_run: bool = False
    wants_post: bool = False
    check: bool = False
    reset: bool = False
    change_channel: bool = False
    schedule: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Request:
        return Request(**{k: v for k, v in dict(data).items() if k in Request.__dataclass_fields__})


def parse(text: str, metadata: Mapping[str, Any] | None = None) -> Request:
    message = text or ""
    head = message.split("\n", 1)[0] if "\t" in message or "|" in message else message  # never read names from a table
    dry_run = bool(_DRY_RUN.search(head))
    return Request(
        sheet=_sheet(head), tab=_tab(head), channel=_channel(head),
        json_mode=bool(_JSON.search(head)) or _wants_json(metadata), dry_run=dry_run,
        wants_post=(bool(_WANTS_POST.search(head)) or bool(_CHANNEL.search(head))) and not dry_run,
        check=bool(_CHECK.search(head)), reset=bool(_RESET.search(head)),
        change_channel=bool(_CHANGE_CHANNEL.search(head)), schedule=bool(_SCHEDULE.search(head)),
    )


def mentions_domain(text: str) -> bool:
    return bool(_DOMAIN.search(text or ""))


def _sheet(text: str) -> str | None:
    for pattern in _SHEET_PATTERNS:
        for found in pattern.finditer(text):
            name = found.group(1).strip(" .'\"")
            if name and name.casefold() not in _STOP_NAMES and not name.startswith("#"):
                return name
    return None


def _tab(text: str) -> str | None:
    found = _TAB.search(text)
    return found.group(1).strip() if found else None


def _channel(text: str) -> str | None:
    found = _CHANNEL.search(text) or _CHANNEL_WORDS.search(text)
    return found.group(1).rstrip(".") if found else None


def _wants_json(metadata: Mapping[str, Any] | None) -> bool:
    return isinstance(metadata, Mapping) and str(metadata.get("output") or "").casefold() == "json"
