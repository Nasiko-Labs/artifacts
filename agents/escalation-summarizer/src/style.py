"""Pure: the optional instruction ("for the VP of Engineering", "tone: urgent", "max 80 words", "shorter")
read into a style. Style changes emphasis, order and wording only: it never supplies a fact."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

DEFAULT_MAX_WORDS = 120
MIN_MAX_WORDS = 30
SHORTER_FACTOR = 2
AUDIENCE_MAX_WORDS = 6

_AUDIENCE = re.compile(r"\bfor (?:the |our |my |a |an )?(?P<who>[A-Za-z][\w&/ .'-]{1,60}?)\s*(?:[,.;:!?\n]|$|"
                       r"\b(?:tone|max|in|as|please|and keep|keep)\b)", re.IGNORECASE)
_TONE = re.compile(r"\btone\s*[:=]?\s*(?P<tone>neutral|urgent)\b|\b(?P<bare>urgent|more urgent)\b", re.IGNORECASE)
_MAX = re.compile(r"\b(?:max(?:imum)?|at most|under|in)\s*(?P<n>\d{2,3})\s*words\b", re.IGNORECASE)
_SHORTER = re.compile(r"\b(?:shorter|briefer|more concise|tighter|condense|tl;?dr)\b", re.IGNORECASE)
_NOT_AUDIENCE = re.compile(r"^(?:me|this|that|it|now|today|json|the thread|this thread|context)$", re.IGNORECASE)
_EXEC = re.compile(r"\b(?:vp|ceo|cto|cfo|coo|cio|exec|executive|leadership|director|head|board|president|chief)\b",
                   re.IGNORECASE)
_ENGINEER = re.compile(r"\b(?:engineer|engineers|engineering team|dev|devs|developers|on-?call|sre)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Style:
    audience: str = ""
    tone: str = "neutral"
    max_words: int = DEFAULT_MAX_WORDS

    def to_dict(self) -> dict:
        return {"audience": self.audience, "tone": self.tone, "max_words": self.max_words}

    @staticmethod
    def from_dict(data: dict | None) -> Style:
        data = data or {}
        return Style(str(data.get("audience") or ""), str(data.get("tone") or "neutral"),
                     int(data.get("max_words") or DEFAULT_MAX_WORDS))

    def describe(self) -> str:
        """The style line given to the compose call (code-built; the raw instruction never reaches it)."""
        lines = [f"Audience: {self.audience}. {_emphasis(self.audience)}" if self.audience else "",
                 "Tone: urgent (wording only; the content stays the same)." if self.tone == "urgent" else "",
                 f"Length: the summary is at most {self.max_words} words."]
        return "\n".join(line for line in lines if line)


def parse(instruction: str, base: Style | None = None) -> Style:
    """Reads the instruction on top of `base` (the stored style for a follow-up, else the default)."""
    style = base or Style()
    text = instruction or ""
    audience = _audience(text)
    if audience:
        style = replace(style, audience=audience)
    tone = _TONE.search(text)
    if tone:
        style = replace(style, tone="urgent" if (tone.group("tone") or "urgent").casefold() != "neutral"
                        else "neutral")
    cap = _MAX.search(text)
    if cap:
        style = replace(style, max_words=max(MIN_MAX_WORDS, min(DEFAULT_MAX_WORDS, int(cap.group("n")))))
    elif _SHORTER.search(text):
        style = replace(style, max_words=max(MIN_MAX_WORDS, style.max_words // SHORTER_FACTOR))
    return style


def changes_style(text: str) -> bool:
    return bool(_audience(text) or _TONE.search(text or "") or _MAX.search(text or "") or _SHORTER.search(text or ""))


def allowed_words(style: Style) -> frozenset[str]:
    """Words of the audience that may appear capitalised in the output without being in the thread."""
    return frozenset(w.casefold().strip(".,'") for w in style.audience.split())


def _audience(text: str) -> str:
    match = _AUDIENCE.search(text or "")
    if not match:
        return ""
    who = " ".join(match.group("who").split()).strip(" .'-")
    if not who or len(who.split()) > AUDIENCE_MAX_WORDS or _NOT_AUDIENCE.match(who):
        return ""
    return f"the {who}" if not who.casefold().startswith("the ") else who


def _emphasis(audience: str) -> str:
    if _EXEC.search(audience):
        return "Lead with the customer impact and the ask; drop technical detail."
    if _ENGINEER.search(audience):
        return "Lead with the technical status and what has been tried."
    return "Keep the standard order."
