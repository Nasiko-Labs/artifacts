"""Pure: the reply text. By default the translation, then one short footer line; "as json" (or metadata
{"output": "json"}) gives the fenced envelope; an envelope from another agent comes back as that envelope
with its fields translated. Nothing here adds HTML or images."""

from __future__ import annotations

import json

import languages

HINT = 'Say "more formal", "now in French", "show the original".'
REVIEW = "A person should review this before it is used."
MAX_FOOTER_NOTES = 4


def fenced_json(data: dict) -> str:
    return "```json\n" + json.dumps(data, ensure_ascii=False, indent=2) + "\n```"


def as_json(envelope: dict, translated_input: dict | None) -> str:
    data = {**envelope, **({"translated_envelope": translated_input} if translated_input else {})}
    return fenced_json(data)


def reply(envelope: dict, translated_input: dict | None, json_mode: bool) -> str:
    if json_mode:
        return as_json(envelope, translated_input)
    body = fenced_json(translated_input) if translated_input else envelope["translation"]
    return f"{body}\n\n{footer(envelope)}"


def footer(envelope: dict) -> str:
    target = languages.BY_CODE[envelope["target_language"].casefold()].name
    head = f"{target} ({envelope['register']}) from {languages.name_for(envelope['source_language'])}."
    notes = [_note(n) for n in envelope.get("notes") or ()][:MAX_FOOTER_NOTES]
    pieces = [head]
    if notes:
        pieces.append("Notes: " + " ".join(notes))
    if envelope.get("confidence") != "high":
        pieces.append(f"Confidence: {envelope.get('confidence')}.")
    if envelope.get("needs_human"):
        pieces.append(REVIEW)
    pieces.append(HINT)
    return "_" + " ".join(pieces).replace("_", "\\_") + "_"


def _note(note: dict) -> str:
    text = str(note.get("note") or "").strip().rstrip(".") + "."
    term = str(note.get("term") or "").strip()
    return f"\"{term}\": {text}" if term else text


def original(source: str, translated_input_source: dict | None) -> str:
    return fenced_json(translated_input_source) if translated_input_source else source
