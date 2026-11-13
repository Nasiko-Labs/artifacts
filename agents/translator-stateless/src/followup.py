"""Pure: a short message about the last translation (rebuilt from the history; nothing is stored). "show the original" and "as json" cost no call;
"more formal", "now in French", "keep 'X' as is" re-translate the stored source with the change applied.
A short message is a follow-up only when nothing is left once language names, register words, glossary
entries and filler are removed, so a short text such as "I speak German" is still text to translate."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

import languages
import register as reg
import request

FOLLOWUP_MAX_WORDS = 12
_ORIGINAL = re.compile(r"^(?:show|see|give|display)?\s*(?:me\s+)?(?:the\s+)?(?:original|source)(?: text| again)?$")
_JSON = re.compile(r"^(?:(?:show|give)(?: me)?(?: it| that| the result)?\s+)?(?:as|in)?\s*json(?: only| please)?$")
_FILLER = re.compile(
    r"\b(?:now|again|please|pls|in|into|to|make|it|this|that|the|same|a|bit|little|more|less|much|and|but|"
    r"translate|translation|version|tone|register|instead|also|then|do|can|you|could|same text|use|with|"
    r"one|thanks|ok|okay|traditional|simplified)\b", re.IGNORECASE)
_REGISTER_WORDS = re.compile(r"\b(?:formal|polite|business|professional|casual|informal|friendly|relaxed|"
                             r"colloquial|neutral|vous|sie|usted|tu|tú|du)\b", re.IGNORECASE)
_PUNCT = re.compile(r"[^\w\s]")


@dataclass(frozen=True)
class FollowUp:
    action: str                     # original | json | retranslate
    job: request.Job | None = None


def parse(text: str, stored: request.Job | None) -> FollowUp | None:
    """With nothing recalled from the history (`stored` None) a follow-up still parses, on an empty job: the
    caller then says the earlier text is gone instead of treating "more formal" as text to translate."""
    stored = stored or request.Job()
    message = " ".join((text or "").split())
    if not message or len(message.split()) > FOLLOWUP_MAX_WORDS:
        return None
    plain = _PUNCT.sub(" ", message.casefold())
    plain = " ".join(plain.split())
    if _ORIGINAL.match(plain):
        return FollowUp("original")
    if _JSON.match(plain):
        return FollowUp("json")
    keep, pairs = request.glossary(message)
    if not (keep or pairs) and not _only_settings(message):
        return None
    return FollowUp("retranslate", request.apply_instruction(replace(stored, as_json=False), message))


def _only_settings(message: str) -> bool:
    if languages.find(message)[0] is None and reg.parse(message) is None:
        return False
    rest = _FILLER.sub(" ", _REGISTER_WORDS.sub(" ", languages.strip_names(message)))
    return not _PUNCT.sub(" ", rest).split()
