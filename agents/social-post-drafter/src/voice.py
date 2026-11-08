"""Pure: the voice profile ("my voice"), kept per conversation in the checkpointer.

A profile holds descriptors only. A pasted sample is read by one model call and validated here; the
sample's text is never stored and never shown to the drafting call, so a fact in it cannot reach a post.
Plain settings ("casual, no emoji, no hashtags, third person") are parsed here in code."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any

from rules import DEFAULT_HASHTAGS

FORMALITY = {1: "very casual", 2: "casual", 3: "neutral", 4: "formal", 5: "very formal"}
MAX_DESCRIPTOR_CHARS = 60
MAX_DESCRIPTORS = 4
_DESCRIPTOR_BAD = re.compile(r"\d|(?<=\s)[A-Z]|^[A-Z][a-z]+[A-Z]")


@dataclass(frozen=True)
class Voice:
    formality: int = 3
    first_person: bool = True
    emoji: bool = False
    hashtags: int = DEFAULT_HASHTAGS
    sentence_length: str = "mixed"     # short | mixed
    openers: tuple[str, ...] = ()
    avoid: tuple[str, ...] = ()
    source: str = "default"            # default | sample | settings

    def to_dict(self) -> dict:
        return {**asdict(self), "openers": list(self.openers), "avoid": list(self.avoid)}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Voice:
        if not data:
            return Voice()
        known = {k: data[k] for k in Voice.__dataclass_fields__ if k in data}
        known["openers"] = tuple(known.get("openers") or ())
        known["avoid"] = tuple(known.get("avoid") or ())
        known["formality"] = int(known.get("formality") or 3)
        known["hashtags"] = int(known.get("hashtags") if known.get("hashtags") is not None else DEFAULT_HASHTAGS)
        return Voice(**known)

    def descriptors(self) -> str:
        lines = [f"formality: {self.formality}/5 ({FORMALITY[self.formality]})",
                 f"person: {'first person (I/we)' if self.first_person else 'third person (the company/team)'}",
                 f"emoji: {'allowed, at most 2' if self.emoji else 'never'}",
                 f"hashtags: at most {self.hashtags}" if self.hashtags else "hashtags: none",
                 f"sentences: {'short' if self.sentence_length == 'short' else 'mixed length'}"]
        if self.openers:
            lines.append("opens posts by: " + "; ".join(self.openers))
        if self.avoid:
            lines.append("avoids: " + "; ".join(self.avoid))
        return "\n".join(f"- {line}" for line in lines)

    def summary(self) -> str:
        tags = f"up to {self.hashtags} hashtags" if self.hashtags else "no hashtags"
        person = "" if self.first_person else ", third person"
        return f"{FORMALITY[self.formality]}, {'emoji ok' if self.emoji else 'no emoji'}, {tags}{person}"


def from_sample(data: Mapping[str, Any] | None) -> Voice | None:
    """The model's descriptors, validated; anything unprovable falls back to the default."""
    if not isinstance(data, Mapping):
        return None
    base = Voice()
    hashtags = data.get("hashtags")
    return Voice(
        formality=_int(data.get("formality"), base.formality, 1, 5),
        first_person=_flag(data.get("first_person"), base.first_person),
        emoji=_flag(data.get("emoji"), base.emoji),
        hashtags=_int(hashtags, base.hashtags, 0, DEFAULT_HASHTAGS),
        sentence_length=data.get("sentence_length") if data.get("sentence_length") in ("short", "mixed") else "mixed",
        openers=descriptors(data.get("openers")), avoid=descriptors(data.get("avoid")), source="sample")


def descriptors(value: Any) -> tuple[str, ...]:
    """Style descriptors only: one longer than 60 characters, or carrying a digit or a capitalised name,
    is dropped (so no fact of the sample survives)."""
    if not isinstance(value, list):
        return ()
    kept = [v.strip() for v in value if isinstance(v, str) and 0 < len(v.strip()) <= MAX_DESCRIPTOR_CHARS]
    return tuple(v for v in kept if not _DESCRIPTOR_BAD.search(v))[:MAX_DESCRIPTORS]


def _int(value: Any, default: int, low: int, high: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if low <= number <= high else default


def _flag(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


# --- plain settings, parsed in code ---------------------------------------------------------------

_SETTINGS = (
    (r"very casual", "formality", 1), (r"very formal", "formality", 5),
    (r"casual|informal|relaxed|friendly|conversational", "formality", 2), (r"neutral", "formality", 3),
    (r"formal|professional", "formality", 4),
)
_FLAGS = (
    (r"no emojis?|without emojis?|never use emojis?", "emoji", False),
    (r"(?:use|with|add|some) emojis?|emojis? (?:are )?(?:ok|fine)", "emoji", True),
    (r"no hashtags?|without hashtags?|drop the hashtags?|remove the hashtags?", "hashtags", 0),
    (r"third person", "first_person", False), (r"first person", "first_person", True),
    (r"short sentences|punchy sentences", "sentence_length", "short"),
)
_TAG_COUNT = re.compile(r"\b(?:max(?:imum)?|at most|up to)?\s*([0-3]|one|two|three) hashtags?\b", re.IGNORECASE)
_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3}
_FILLER = re.compile(
    r"\b(?:always|from now on|in general|by default|going forward|all my posts|please|and|my|your|the|a|an|voice|"
    r"tone|style|be|make|it|them|write|posts?|i|want|prefer|use|keep|set|to|more|less|with|you|should|sound|in|for|"
    r"future|everything|all|max(?:imum)?|at most|up to)\b|[\s,.;:!-]+",
    re.IGNORECASE,
)


def settings_patch(text: str) -> dict:
    patch: dict = {}
    lowered = (text or "").casefold()
    for pattern, key, value in _SETTINGS:
        if re.search(rf"\b(?:{pattern})\b", lowered):
            patch[key] = value
            break
    for pattern, key, value in _FLAGS:
        if re.search(rf"\b(?:{pattern})\b", lowered):
            patch[key] = value
    if "hashtags" not in patch and (match := _TAG_COUNT.search(lowered)):
        raw = match.group(1)
        patch["hashtags"] = _NUMBER_WORDS.get(raw, int(raw) if raw.isdigit() else DEFAULT_HASHTAGS)
    return patch


def strip_settings(text: str) -> str:
    """The message without its voice settings ("voice: casual, no emoji")."""
    residual = _TAG_COUNT.sub(" ", re.sub(r"\b(?:voice|tone|style)\s*:", " ", text or "", flags=re.IGNORECASE))
    for pattern in (*(p for p, _, _ in _SETTINGS), *(p for p, _, _ in _FLAGS)):
        residual = re.sub(rf"\b(?:{pattern})\b", " ", residual, flags=re.IGNORECASE)
    return residual


def settings_only(text: str, patch: Mapping[str, Any]) -> bool:
    """True when the message carries nothing but voice settings (so it needs no model call)."""
    return bool(patch) and not _FILLER.sub("", strip_settings(text)).strip()


def apply(voice: Voice, patch: Mapping[str, Any], source: str = "settings") -> Voice:
    if not patch:
        return voice
    return replace(voice, **dict(patch), source="sample" if voice.source == "sample" else source)
