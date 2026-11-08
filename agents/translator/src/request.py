"""Pure: one translation job read from the user's message, before any LLM call.

The instruction is the first line when it carries a cue ("Translate to Spanish, formal: <text>", "in formal
Japanese:" + text on the next lines) or the last line after the text. The text after the cue's colon (or
the other lines) is the source; a JSON object with translatable string fields is an envelope from another
agent. Target language, register and glossary are read from the instruction only, never from the text."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from typing import Any

import languages
import register as reg

INSTRUCTION_MAX_CHARS = 200
MAX_GLOSSARY = 20
ENVELOPE_FIELDS = ("draft", "alt_shorter", "text", "memo_markdown", "tldr", "summary")

_Q = "\"'“”‘’"
_TRANSLATE = re.compile(r"\btranslat\w*", re.IGNORECASE)
_LEADING = re.compile(r"^\s*(?:now|and|please|then)?\s*(?:in|into|to)\s+\S", re.IGNORECASE)
_COLON = re.compile(r":(?:\s|$)")
_INLINE = re.compile(rf"^\s*translate\s+[{_Q}](.+)[{_Q}]\s+(?:in|into|to)\s+[^:]+$", re.IGNORECASE)
_KEEP = (re.compile(rf"\b(?:keep|leave|don'?t translate|do not translate)\s+[{_Q}]([^{_Q}\n]{{1,60}})[{_Q}]",
                    re.IGNORECASE),
         re.compile(r"\b(?:keep|leave)\s+([^\s'\"]{1,40})\s+(?:untranslated|as is|unchanged|in english)\b",
                    re.IGNORECASE))
_AS = re.compile(rf"\btranslate\s+[{_Q}]([^{_Q}\n]{{1,60}})[{_Q}]\s+as\s+[{_Q}]([^{_Q}\n]{{1,60}})[{_Q}]",
                 re.IGNORECASE)
_JSON_WORDS = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


@dataclass(frozen=True)
class Job:
    """What one translation needs. `source` is plain text; `envelope` replaces it for agent output."""

    source: str = ""
    envelope: dict | None = None
    target: str = ""
    target_note: str = ""
    unknown: str = ""
    register: str = reg.NEUTRAL
    keep: tuple[str, ...] = ()
    glossary: tuple[tuple[str, str], ...] = ()
    source_hint: str = ""
    as_json: bool = False

    @property
    def has_text(self) -> bool:
        return bool(self.source.strip()) or bool(self.envelope)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Any) -> Job | None:
        if not isinstance(data, dict):
            return None
        envelope = data.get("envelope")
        return Job(source=str(data.get("source") or ""), envelope=envelope if isinstance(envelope, dict) else None,
                   target=str(data.get("target") or ""), target_note=str(data.get("target_note") or ""),
                   unknown=str(data.get("unknown") or ""), register=str(data.get("register") or reg.NEUTRAL),
                   keep=tuple(str(k) for k in data.get("keep") or ()),
                   glossary=tuple((str(a), str(b)) for a, b in data.get("glossary") or ()),
                   source_hint=str(data.get("source_hint") or ""), as_json=bool(data.get("as_json")))


def is_cue(line: str) -> bool:
    if len(line) > INSTRUCTION_MAX_CHARS:
        return False
    return bool(_TRANSLATE.search(line)) or (bool(_LEADING.match(line)) and languages.find(line)[0] is not None)


def split(text: str) -> tuple[str, str]:
    """(instruction, body). No cue: ("", the whole text)."""
    stripped = (text or "").strip()
    first, _, rest = stripped.partition("\n")
    colon = _COLON.search(first)
    if colon and is_cue(first[:colon.start()]):  # the text after the colon may be one long line
        return first[:colon.start()].strip(), (first[colon.end():] + ("\n" + rest if rest else "")).strip()
    if is_cue(first):
        inline = _INLINE.match(first)
        if inline and not rest:
            return first, inline.group(1).strip()
        return first.strip(), rest.strip()
    head, _, last = stripped.rpartition("\n")
    if head and is_cue(last):
        return last.strip().rstrip(":"), head.strip()
    return "", stripped


def envelope_from(body: str) -> dict | None:
    fenced = _FENCE.match(body.strip())
    try:
        data = json.loads(fenced.group(1) if fenced else body)
    except ValueError:
        return None
    if isinstance(data, dict) and any(isinstance(data.get(f), str) and data[f].strip() for f in ENVELOPE_FIELDS):
        return data
    return None


def glossary(instruction: str) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    pairs = tuple((a.strip(), b.strip()) for a, b in _AS.findall(instruction or ""))[:MAX_GLOSSARY]
    rest = _AS.sub(" ", instruction or "")
    keep: list[str] = []
    for pattern in _KEEP:
        keep += [k.strip() for k in pattern.findall(rest) if k.strip() and k.strip() not in keep]
    return tuple(keep[:MAX_GLOSSARY - len(pairs)]), pairs


def apply_instruction(job: Job, instruction: str) -> Job:
    """The instruction's language, register, glossary and JSON wish layered over `job`."""
    target, source = languages.find(instruction)
    keep, pairs = glossary(instruction)
    changes: dict[str, Any] = {}
    if target is not None:
        changes.update(target=target.language.code, target_note=target.note, unknown="")
    elif (unknown := unknown_language(instruction)) and not job.target:
        changes["unknown"] = unknown
    if source is not None:
        changes["source_hint"] = source.language.name
    if (register := reg.parse(instruction)) is not None:
        changes["register"] = register
    if keep or pairs:
        changes.update(keep=tuple(dict.fromkeys((*job.keep, *keep)))[:MAX_GLOSSARY],
                       glossary=(*job.glossary, *pairs)[:MAX_GLOSSARY])
    if _JSON_WORDS.search(instruction or ""):
        changes["as_json"] = True
    return replace(job, **changes)


_UNKNOWN = re.compile(r"\b(?:into|to)\s+([A-Z][\w-]{2,30}|[a-z][\w-]{3,30})\b")
_NOT_LANGUAGE = frozenset({"this", "that", "the", "them", "it", "me", "my", "our", "your", "formal", "casual",
                           "neutral", "polite", "informal", "english", "text", "email", "please", "a", "an"})


def unknown_language(instruction: str) -> str:
    """A word after "to"/"into" that is not a known language, e.g. "translate to Klingon"."""
    found = _UNKNOWN.search(instruction or "")
    if not found or found.group(1).casefold() in _NOT_LANGUAGE:
        return ""
    return found.group(1)


def wants_json(text: str) -> bool:
    return bool(_JSON_WORDS.search(text or ""))
