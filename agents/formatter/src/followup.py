"""Pure: reads the instruction words around the JSON (target, compact, no footer, title, as json,
plain-text version) and decides whether a message is only a follow-up ("as slack", "compact") on the
stored input. Words only; no model."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from model import EMAIL, MARKDOWN, SLACK, Options

_TITLE = re.compile(r"\btitle\s*:\s*([^\n,;]+)", re.IGNORECASE)
_PLAIN_VERSION = re.compile(r"\bplain[- ]?text(?: version| alternative)?\b|\btext version\b", re.IGNORECASE)
_AS_JSON = re.compile(r"\b(?:as|in|to) json\b|\bjson only\b|\braw json\b|\bthe envelope\b", re.IGNORECASE)
_NO_FOOTER = re.compile(r"\bno[- ]footer\b|\bwithout (?:the |a )?footer\b|\bdrop the footer\b", re.IGNORECASE)
_WITH_FOOTER = re.compile(r"\bwith (?:the |a )?footer\b", re.IGNORECASE)
_COMPACT = re.compile(r"\bcompact\b|\bbullets only\b|\bno tables?\b", re.IGNORECASE)
_FULL = re.compile(r"\b(?:full|with tables?|not compact)\b", re.IGNORECASE)
_TARGETS = (
    (SLACK, re.compile(r"\b(?:slack|block ?kit|blocks|mrkdwn)\b", re.IGNORECASE)),
    (EMAIL, re.compile(r"\b(?:e-?mail|html)\b", re.IGNORECASE)),
    (MARKDOWN, re.compile(r"\b(?:markdown|md|plain)\b", re.IGNORECASE)),
)
_FILLER = re.compile(
    r"\b(?:as|in|to|into|for|now|please|pls|format|formatted|render|rendered|convert|converted|turn|it|this|"
    r"that|show|me|the|version|make|give|again|instead|and|then|but|a|an|same|one|thing|output|do|can|you|"
    r"could|would|send|of|with|without|footer|tables?|bullets?|only|no|not|full|text|alternative|kit|"
    r"slack|block|blocks|mrkdwn|e-?mail|html|markdown|md|plain|compact|json|raw|envelope)\b",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[^\w]+")
FOLLOWUP_MAX_CHARS = 160


@dataclass(frozen=True)
class Ask:
    """One message's instruction words. `target` is '' when none was named."""

    target: str = ""
    compact: bool | None = None
    footer: bool | None = None
    title: str | None = None
    as_json: bool = False
    plain_version: bool = False


def read(text: str) -> Ask:
    words = text or ""
    title = _TITLE.search(words)
    rest = _TITLE.sub(" ", words)
    plain_version = bool(_PLAIN_VERSION.search(rest))
    target_words = _PLAIN_VERSION.sub(" ", rest)
    compact = False if _FULL.search(rest) else (True if _COMPACT.search(rest) else None)
    footer = False if _NO_FOOTER.search(rest) else (True if _WITH_FOOTER.search(rest) else None)
    return Ask(target=EMAIL if plain_version else target_of(target_words), compact=compact, footer=footer,
               title=title.group(1).strip() if title else None, as_json=bool(_AS_JSON.search(rest)),
               plain_version=plain_version)


def target_of(text: str) -> str:
    """The first target named in `text` ('' when none)."""
    found = [(m.start(), name) for name, pattern in _TARGETS for m in [pattern.search(text or "")] if m]
    return min(found)[1] if found else ""


def apply(ask: Ask, base: Options) -> Options:
    """`base` with this message's choices on top (a choice not mentioned keeps its stored value)."""
    return replace(
        base,
        target=ask.target or base.target,
        compact=base.compact if ask.compact is None else ask.compact,
        footer=base.footer if ask.footer is None else ask.footer,
        title=base.title if ask.title is None else ask.title,
    )


def is_followup_only(text: str) -> bool:
    """True when the message is nothing but follow-up words (and names at least one choice)."""
    stripped = (text or "").strip()
    if not stripped or len(stripped) > FOLLOWUP_MAX_CHARS:
        return False
    ask = read(stripped)
    named = ask.target or ask.compact is not None or ask.footer is not None or ask.title is not None \
        or ask.as_json or ask.plain_version
    leftover = _PUNCT.sub(" ", _FILLER.sub(" ", _TITLE.sub(" ", stripped))).strip()
    return bool(named) and not leftover


def options_to_state(opts: Options) -> dict:
    return {"target": opts.target, "compact": opts.compact, "footer": opts.footer, "title": opts.title}


def options_from_state(data: dict | None) -> Options:
    data = data or {}
    target = data.get("target") if data.get("target") in (MARKDOWN, SLACK, EMAIL) else MARKDOWN
    return Options(target=target, compact=bool(data.get("compact")), footer=data.get("footer", True) is not False,
                   title=str(data.get("title") or ""))
