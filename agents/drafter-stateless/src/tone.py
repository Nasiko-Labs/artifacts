"""Pure: the tone profile ("your tone"), rebuilt each message from the history (recall.py); never stored.

A profile holds descriptors only. A pasted sample is read by one model call and validated here; the
sample's text is never stored, so a fact in it cannot reach a draft. Plain settings ("casual, no
emoji, sign off as A.") are parsed here in code."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any

from register import LENGTHS

FORMALITY = {1: "very casual", 2: "casual", 3: "neutral", 4: "formal", 5: "very formal"}
MAX_PHRASE_CHARS = 60
MAX_QUIRKS = 5
NONE = "none"


@dataclass(frozen=True)
class Profile:
    formality: int = 3
    length: str = "medium"
    greeting: str = "Hi {first},"
    sign_off: str = "Thanks,\n[your name?]"
    emoji: bool = False
    contractions: bool = True
    bullets_ok: bool = True
    quirks: tuple[str, ...] = ()
    source: str = "default"            # default | sample | settings

    def to_dict(self) -> dict:
        return {**asdict(self), "quirks": list(self.quirks)}

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Profile:
        if not data:
            return Profile()
        known = {k: data[k] for k in Profile.__dataclass_fields__ if k in data}
        known["quirks"] = tuple(known.get("quirks") or ())
        known["formality"] = int(known.get("formality") or 3)
        return Profile(**known)

    def descriptors(self) -> str:
        lines = [f"formality: {self.formality}/5 ({FORMALITY[self.formality]})", f"usual length: {self.length}",
                 f"greeting: {self.greeting}", f"sign-off: {self.sign_off}",
                 f"emoji: {'allowed' if self.emoji else 'never'}",
                 f"contractions: {'yes' if self.contractions else 'no'}",
                 f"bullets: {'fine' if self.bullets_ok else 'avoid'}"]
        if self.quirks:
            lines.append("habits: " + "; ".join(self.quirks))
        return "\n".join(f"- {line}" for line in lines)

    def summary(self) -> str:
        return f"{FORMALITY[self.formality]} ({self.formality}/5), {self.length} messages, " + (
            "emoji ok" if self.emoji else "no emoji")


def from_sample(data: Mapping[str, Any], sample: str) -> Profile:
    """Validate the model's descriptors against the sample; anything unprovable falls back to default."""
    base = Profile()
    folded = _fold(sample)
    return Profile(
        formality=_formality(data.get("formality"), base.formality),
        length=data.get("length") if data.get("length") in LENGTHS else base.length,
        greeting=_phrase(data.get("greeting"), folded, base.greeting),
        sign_off=_phrase(data.get("sign_off"), folded, base.sign_off),
        emoji=_flag(data.get("emoji"), base.emoji),
        contractions=_flag(data.get("contractions"), base.contractions),
        bullets_ok=_flag(data.get("bullets_ok"), base.bullets_ok),
        quirks=_quirks(data.get("quirks")),
        source="sample",
    )


def _formality(value: Any, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if 1 <= number <= 5 else default


def _flag(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _phrase(value: Any, folded_sample: str, default: str) -> str:
    """A greeting or sign-off is kept only if the sample really uses it (the {first} slot aside)."""
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_PHRASE_CHARS:
        return default
    if value.strip().casefold() == NONE:
        return NONE
    pieces = [_fold(p).strip(" ,.!-") for p in value.split("{first}")]
    return value.strip() if all(p in folded_sample for p in pieces if p) else default


def _quirks(value: Any) -> tuple[str, ...]:
    """Style habits only: one carrying a digit or a capitalised word (a name, a project) is dropped."""
    if not isinstance(value, list):
        return ()
    kept = [q.strip() for q in value if isinstance(q, str) and 0 < len(q.strip()) <= MAX_PHRASE_CHARS]
    return tuple(q for q in kept if not re.search(r"\d|(?<=\s)[A-Z]", q))[:MAX_QUIRKS]


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold())


# --- plain settings, parsed in code ---------------------------------------------------------------

_ABSOLUTE = ((r"very casual", 1), (r"very formal", 5), (r"casual|informal|relaxed|friendly", 2),
             (r"neutral", 3), (r"formal|professional", 4))
_RELATIVE = ((r"more (?:casual|relaxed|informal)|less formal|warmer|friendlier", -1),
             (r"more (?:formal|professional)|less casual", 1))
_FLAGS = ((r"no emojis?|without emojis?|never use emojis?", "emoji", False),
          (r"(?:use|with|add) emojis?|emojis? (?:are )?(?:ok|fine)", "emoji", True),
          (r"no bullets?|without bullets?", "bullets_ok", False),
          (r"no contractions", "contractions", False),
          (r"no sign-?off", "sign_off", NONE), (r"no greeting", "greeting", NONE))
_SIGN_OFF = re.compile(r"sign(?:ed)?[- ]?off (?:as|with) ([^\n]{1,60}?)\s*(?:[.!]\s*$|$|,\s*and\b|\band\b)", re.IGNORECASE)
_FILLER = re.compile(
    r"\b(?:always|from now on|in general|by default|going forward|all my messages|please|and|my|your|the|a|an|"
    r"tone|style|be|make|it|them|write|messages?|i|want|prefer|use|keep|set|to|more|less|with|you|should|sound|"
    r"in|for|future|everything|everything's|all)\b|[\s,.;:!-]+",
    re.IGNORECASE,
)


def settings_patch(text: str) -> dict:
    """Code-level tone settings in a message ("casual", "more formal", "no emoji", "sign off as A.")."""
    patch: dict = {}
    lowered = (text or "").casefold()
    for pattern, delta in _RELATIVE:
        if re.search(rf"\b(?:{pattern})\b", lowered):
            patch["formality_delta"] = delta
            break
    else:
        for pattern, level in _ABSOLUTE:
            if re.search(rf"\b(?:{pattern})\b", lowered):
                patch["formality"] = level
                break
    for pattern, key, value in _FLAGS:
        if re.search(rf"\b(?:{pattern})\b", lowered):
            patch[key] = value
    if (match := _SIGN_OFF.search(text or "")) is not None:
        patch["sign_off"] = match.group(1).strip()
    if re.search(r"\b(?:shorter|short|brief)\b", lowered):
        patch["length"] = "short"
    elif re.search(r"\b(?:longer|long|detailed)\b", lowered):
        patch["length"] = "long"
    return patch


def settings_only(text: str, patch: Mapping[str, Any]) -> bool:
    """True when the message carries nothing but tone settings (so it needs no model call)."""
    if not patch:
        return False
    residual = _SIGN_OFF.sub(" ", text or "")
    for pattern in (*(p for p, _ in _RELATIVE), *(p for p, _ in _ABSOLUTE), *(p for p, _, _ in _FLAGS),
                    r"shorter|short|brief|longer|long|detailed"):
        residual = re.sub(rf"\b(?:{pattern})\b", " ", residual, flags=re.IGNORECASE)
    return not _FILLER.sub("", residual).strip()


def apply_patch(profile: Profile, patch: Mapping[str, Any]) -> Profile:
    if not patch:
        return profile
    formality = profile.formality + int(patch.get("formality_delta", 0))
    formality = int(patch.get("formality", formality))
    changes = {k: v for k, v in patch.items() if k not in ("formality", "formality_delta")}
    source = "sample" if profile.source == "sample" else "settings"
    return replace(profile, **changes, formality=min(5, max(1, formality)), source=source)
