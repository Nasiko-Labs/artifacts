"""Pure: fencing untrusted text (pasted source, a voice sample, the current post, a previous answer)
before the model sees it. Any marker the user typed is neutralised, so pasted text cannot close a fence
and pose as instructions."""

from __future__ import annotations

import re

NAMES = ("SOURCE", "VOICE_SAMPLE", "CURRENT_POST", "PREVIOUS_ANSWER", "CHUNK")
_MARKER = re.compile(r"<<<\s*[A-Z_]+\s*>>>|>>>\s*END_[A-Z_]+\s*<<<")
REMOVED = "[removed marker]"


def clean(text: str) -> str:
    """User text with every fence-looking marker neutralised."""
    return _MARKER.sub(REMOVED, text or "")


_INJECTION = re.compile(r"ignore (?:all |any )?(?:previous|prior|above) instructions|disregard (?:the|all|previous)"
                        r"|system prompt|you are now|new instructions", re.IGNORECASE)
_SENTENCE = re.compile(r"[^.!?\n]*(?:[.!?]+|\n|$)")


def facts_only(text: str) -> str:
    """Pasted text without the sentences that try to instruct the model: those are not facts, so nothing
    in them (an award, a link) can ground a post."""
    if not _INJECTION.search(text or ""):
        return text or ""
    kept = [s for s in _SENTENCE.findall(text) if not _INJECTION.search(s)]
    return "".join(kept)


def fence(name: str, text: str) -> str:
    if name not in NAMES:
        raise ValueError(f"unknown fence {name!r}")
    return f"<<<{name}>>>\n{clean(text)}\n>>>END_{name}<<<"
