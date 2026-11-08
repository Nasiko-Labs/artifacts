"""Pure: what the user asked, parsed in code: sheet, tab, channel, compare mode, the user's own `context:`
text, dry run, json mode, and the follow-up intents code can recognise without a model call."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

MAX_CONTEXT_CHARS = 500
_NAME = r"[A-Za-z0-9][\w&'./ -]{0,60}?"
_SHEET_PATTERNS = (
    re.compile(r"[\"“']([^\"”']{1,80})[\"”']\s+(?:sheet|spreadsheet)\b", re.IGNORECASE),
    re.compile(r"\b(?:sheet|spreadsheet)\s+(?:named|called|titled)\s+[\"“']?(" + _NAME + r")[\"”']?"
               r"(?=$|[,.;!?]|\s+(?:and|then|tab|vs|versus|to|for)\b)", re.IGNORECASE),
    re.compile(r"\b(?:from|in|of|using|read|on)\s+(?:the\s+|my\s+|our\s+)?(" + _NAME + r")\s+(?:sheet|spreadsheet)\b",
               re.IGNORECASE),
    re.compile(r"\b(?:from|in|using)\s+[\"“]([^\"”]{1,80})[\"”]", re.IGNORECASE),
)
_TAB = re.compile(r"\b(?:tab|worksheet)\s+(?:named\s+|called\s+)?[\"“']?([\w &-]{1,40}?)[\"”']?(?=$|[,.;!?]|\s+(?:and|"
                  r"then|vs|to|for|from|of)\b)", re.IGNORECASE)
_CHANNEL = re.compile(r"(?<![\w&])#([A-Za-z0-9][\w.-]{0,79})")
_CHANNEL_WORDS = re.compile(r"\b(?:post|share|send)\b(?: it| this| that| the (?:draft|narrative|paragraph))?"
                            r" (?:to|in|into) (?:the )?([A-Za-z0-9][\w.-]{0,79}) channel\b", re.IGNORECASE)
_WANTS_POST = re.compile(r"\b(post|share|send)\b(?: (?:it|this|that|the (?:draft|narrative|paragraph)))?"
                         r"(?: (?:to|in|into)\b|$|[.!])|#[A-Za-z0-9]", re.IGNORECASE)
_DRY_RUN = re.compile(r"\b(?:don'?t|do not|never|no need to)\s+post\b|\bdry[ -]?run\b|\bwithout posting\b|"
                      r"\bjust (?:the )?draft\b|\bdraft only\b", re.IGNORECASE)
_MONTH = re.compile(r"\b(?:vs\.?|versus|compared? (?:to|with)|against)\s+(?:the\s+)?(?:same week\s+)?last month\b|"
                    r"\bsame week last month\b|\bmonth ago\b|\b(?:4|four) weeks? (?:ago|back)\b", re.IGNORECASE)
_PREVIOUS = re.compile(r"\b(?:vs\.?|versus|compared? (?:to|with))\s+(?:the\s+)?(?:last|previous|prior) (?:week|period)\b",
                       re.IGNORECASE)
_CONTEXT = re.compile(r"\bcontext\s*:\s*([^\n]+)", re.IGNORECASE)
_JSON = re.compile(r"\b(?:as|in) json\b|\bjson only\b|\bjson output\b", re.IGNORECASE)
_NARRATE = re.compile(r"\b(narrat\w*|summari[sz]e|what (?:happened|changed)|this week'?s numbers|weekly numbers|"
                      r"kpis?|metrics?)\b", re.IGNORECASE)
_REWRITE = re.compile(r"\b(shorter|longer|rewrite|reword|rephrase|more (?:formal|casual|concise|upbeat|neutral|"
                      r"direct)|less (?:formal|wordy)|simpler|punchier|friendlier|tone|tighten|trim it)\b",
                      re.IGNORECASE)
_WHY = re.compile(r"^\s*why\b|\bwhy (?:did|does|is|was|were|has)\b|\bwhat (?:caused|drove|explains)\b",
                  re.IGNORECASE)
_DOMAIN = re.compile(r"\b(numbers?|metrics?|kpis?|sheet|spreadsheet|week(?:ly)?|narrat\w*|signups?|mrr|arr|churn|"
                     r"revenue|table|period|post|draft|movers?|wow|week over week|dashboard|channel)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    sheet: str | None = None
    tab: str | None = None
    channel: str | None = None
    compare: str = "previous"          # "previous" | "month"
    compare_named: bool = False
    context: str | None = None
    dry_run: bool = False
    json_mode: bool = False
    wants_post: bool = False
    narrate: bool = False
    rewrite: bool = False
    why: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> Request:
        return Request(**{k: v for k, v in dict(data).items() if k in Request.__dataclass_fields__})


def parse(text: str, metadata: Mapping[str, Any] | None = None) -> Request:
    message = text or ""
    context_match = _CONTEXT.search(message)
    context = context_match.group(1).strip()[:MAX_CONTEXT_CHARS] if context_match else None
    head = without_context(message)  # names are never read from the user's context text
    month, previous = bool(_MONTH.search(head)), bool(_PREVIOUS.search(head))
    dry_run = bool(_DRY_RUN.search(head))
    return Request(
        sheet=_sheet(head), tab=_tab(head), channel=_channel(head),
        compare="month" if month else "previous", compare_named=month or previous,
        context=context or None, dry_run=dry_run,
        json_mode=bool(_JSON.search(message)) or _wants_json(metadata),
        wants_post=bool(_WANTS_POST.search(head)) and not dry_run,
        narrate=bool(_NARRATE.search(head)), rewrite=bool(_REWRITE.search(head)), why=bool(_WHY.search(head)),
    )


def without_context(text: str) -> str:
    return _CONTEXT.sub(" ", text or "")


def mentions_domain(text: str) -> bool:
    return bool(_DOMAIN.search(text or "")) or bool(_CONTEXT.search(text or ""))


def _sheet(text: str) -> str | None:
    for pattern in _SHEET_PATTERNS:
        found = pattern.search(text)
        if found:
            name = found.group(1).strip(" .'\"")
            if name and name.casefold() not in {"my", "the", "our", "this", "that", "a", "weekly", "metrics"}:
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
