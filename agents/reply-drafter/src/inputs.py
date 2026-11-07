"""Pure: reading one user message into the incoming message, the user's instruction (purpose and extras)
and their past replies (examples). Sections start at a cue ("reply to this:", "my past replies:"); the
instruction is the text before the first cue plus a trailing instruction paragraph or clause."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field

from incoming import Incoming, envelope_text, has_headers, header_start, read_incoming
from register import detect_channel

MAX_EXAMPLES = 3
MAX_EXAMPLE_CHARS = 1_500
MAX_TAIL_CHARS = 300

# Direct chat sends "{history}\n\nCurrent message: {text}" once a session has prior turns.
_CURRENT_MESSAGE_MARKER = "\n\nCurrent message: "
_KIND = r"(?:e-?mail|mail|slack|teams|chat|dm|text|sms|whats ?app)"
_INCOMING_CUE = re.compile(
    rf"\b(?:(?:reply|respond|answer|write back) to(?: (?:this|the following|that|her|his|their))?(?: {_KIND})?"
    rf"(?: (?:message|msg|email|text|note|dm))?(?: from (?P<who>[A-Za-z][\w'’-]*(?: [A-Z][\w'’-]*)?))?"
    r"|(?:reply|respond|write back) to (?P<to>(?!(?:this|that|them|her|him|it)\b)[A-Za-z][\w'’-]*)"
    r"|incoming(?: message)?|(?:they|she|he) (?:wrote|said|sent)|here(?:'s| is) (?:the|their|her|his) "
    r"(?:email|message))\s*:", re.IGNORECASE)
_EXAMPLES_CUE = re.compile(
    r"(?:\bmy (?:past|previous|old|earlier|recent|usual) (?:replies|reply|messages|emails)|\bpast replies"
    r"|\bhere(?:'s| is| are) how i (?:reply|write|respond)|\bhow i (?:usually )?(?:reply|write)"
    r"|\bmy replies|\bexamples?(?: of (?:my|how i) (?:replies|reply|write|writing))?)\s*:"
    r"|^\s*example\s*1\s*[:.)\-]", re.IGNORECASE | re.MULTILINE)
_INSTR = (r"(?:say|decline|accept|agree|offer|propose|suggest|refuse|reject|turn (?:it|them|this|that) down"
          r"|push back|apologi[sz]e|thank (?:them|her|him)|tell (?:them|her|him)|ask (?:them|her|him|for|if|whether)"
          r"|let (?:them|her|him) know|i want to|i'd like to|i need to|purpose\s*:)")
_INSTR_PARAGRAPH = re.compile(rf"^(?:(?:please|politely|just|and)\s+)?{_INSTR}", re.IGNORECASE)
_INSTR_INLINE = re.compile(rf"(?:[,;.!?]|\s[-–—])\s+(?=(?:(?:politely|and)\s+)?{_INSTR}\b)", re.IGNORECASE)
_REPLY_WORD = re.compile(r"\b(?:reply|respond|response|answer)\b", re.IGNORECASE)
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_EXAMPLE_SPLIT = re.compile(r"^\s*(?:-{3,}|={3,}|example\s*\d+\s*[:.)\-]?)\s*$|^\s*example\s*\d+\s*[:.)\-]\s*",
                            re.IGNORECASE | re.MULTILINE)
_SIGN_OFF_AS = re.compile(r"\bsign(?:ed)?[ -]?off (?:as|with)\s+([^\n,;]{1,30}?)\s*(?:[.;,]\s|[.;,]?$|\n)",
                          re.IGNORECASE)
_SHORT = re.compile(r"\b(?:keep it short|short|brief)\b", re.IGNORECASE)
_SUBJECT_WANTED = re.compile(r"\binclude a subject\b|\bwith a subject\b", re.IGNORECASE)
_JSON = re.compile(r"\bas json\b|\bjson only\b|\bin json\b", re.IGNORECASE)
_FILLER = frozenset(
    ["reply", "replies", "respond", "response", "answer", "draft", "write", "back", "to", "this", "that", "the", "following", "a", "an", "me", "my", "please", "for", "email", "e-mail", "mail", "message", "slack", "teams", "chat", "dm", "text", "sms", "whatsapp", "here", "is", "it", "them", "can", "you", "could", "short", "brief", "as", "json", "in", "only", "and", "help", "with", "i", "need", "of", "past", "previous", "examples", "example", "how", "usually", "keep", "subject", "include", "from", "below", "above"])


@dataclass(frozen=True)
class Request:
    """One turn as the graph receives it (a fresh turn, or a resumed pause)."""

    text: str                                               # the user's message, history prefix removed
    org: tuple[str, ...] = ()                               # organization instructions (later text parts)
    slots: Mapping[str, str] = field(default_factory=dict)  # incoming / purpose / channel answered in a pause
    pauses: int = 0
    wants_json: bool = False                                # message metadata {"output": "json"}


@dataclass(frozen=True)
class Parsed:
    incoming: Incoming | None
    instruction: str                  # the user's own words: purpose and extras
    examples: tuple[str, ...]
    channel: str | None
    has_purpose: bool
    short: bool = False
    want_subject: bool = False
    sign_off_as: str = ""
    wants_json: bool = False
    notes: tuple[str, ...] = ()


def current_message(raw: str) -> str:
    idx = (raw or "").rfind(_CURRENT_MESSAGE_MARKER)
    return raw if idx == -1 else raw[idx + len(_CURRENT_MESSAGE_MARKER):]


def parse(text: str) -> Parsed:
    text = (text or "").strip()
    envelope = envelope_text(text)
    if envelope is not None:
        instruction, message = envelope
        return _parsed(read_incoming(message), instruction, (), instruction, ())
    inc, ex = _INCOMING_CUE.search(text), _EXAMPLES_CUE.search(text)
    marks = sorted((m for m in (inc, ex) if m), key=lambda m: m.start())
    head = text[:marks[0].start()] if marks else text
    sections = {("incoming" if m is inc else "examples"): text[m.end():(marks[n + 1].start() if n + 1 < len(marks)
                                                                         else len(text))]
                for n, m in enumerate(marks)}
    last = ("incoming" if marks[-1] is inc else "examples") if marks else "head"
    body, tail = split_tail(sections.get(last, head), inline=last != "examples")
    if last == "head":
        head = body
    else:
        sections[last] = body
    examples, notes = split_examples(sections.get("examples", ""))
    if inc is not None:
        incoming = read_incoming(sections["incoming"], inc.group("who") or inc.group("to") or "")
        return _parsed(incoming, f"{head}\n{tail}", examples, f"{inc.group(0)}\n{head}\n{tail}", notes)
    message, before = _uncued(head, bool(tail or examples))
    incoming = read_incoming(message) if message else None
    return _parsed(incoming, f"{before}\n{tail}", examples, f"{before}\n{tail}", notes)


def _uncued(head: str, other_parts: bool) -> tuple[str, str]:
    """(the incoming message, the instruction) from text with no incoming cue."""
    if has_headers(head):
        start = header_start(head)
        return head[start:].strip(), head[:start].strip()
    paragraphs = [p for p in _PARAGRAPH_BREAK.split(head.strip()) if p.strip()]
    if len(paragraphs) >= 2:
        first = paragraphs[0].strip()
        if len(first.split()) <= 40 and (_INSTR_PARAGRAPH.match(first) or _REPLY_WORD.search(first)):
            return "\n\n".join(paragraphs[1:]), first
        return head.strip(), ""
    if other_parts and head.strip() and not _is_instruction(head):
        return head.strip(), ""
    return "", head.strip()


def _is_instruction(text: str) -> bool:
    stripped = text.strip()
    return len(stripped) <= MAX_TAIL_CHARS and bool(_INSTR_PARAGRAPH.match(stripped))


def split_tail(text: str, inline: bool) -> tuple[str, str]:
    """(the text, a trailing instruction): a last paragraph that starts with an instruction, or (inline)
    the earliest short trailing clause that does ("<pasted message>, decline politely")."""
    stripped = text.strip()
    paragraphs = _PARAGRAPH_BREAK.split(stripped)
    last = paragraphs[-1].strip()
    if len(paragraphs) >= 2 and len(last) <= MAX_TAIL_CHARS and _INSTR_PARAGRAPH.match(last):
        return stripped[:len(stripped) - len(paragraphs[-1])].strip(), last
    if not inline:
        return stripped, ""
    offset = len(stripped) - len(paragraphs[-1])
    for match in _INSTR_INLINE.finditer(stripped, offset):
        if len(stripped) - match.end() <= MAX_TAIL_CHARS:
            return stripped[:match.start() + 1].strip(), stripped[match.end():].strip()
    return stripped, ""


def split_examples(section: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    blocks = [b.strip().strip('"“”').strip() for b in _EXAMPLE_SPLIT.split(section or "")]
    blocks = [b for b in blocks if re.search(r"[A-Za-z]{2,}", b)]
    notes = []
    if len(blocks) > MAX_EXAMPLES:
        notes.append(f"I used your first {MAX_EXAMPLES} examples.")
    if any(len(b) > MAX_EXAMPLE_CHARS for b in blocks[:MAX_EXAMPLES]):
        notes.append(f"I read the first {MAX_EXAMPLE_CHARS:,} characters of each long example.")
    return tuple(b[:MAX_EXAMPLE_CHARS] for b in blocks[:MAX_EXAMPLES]), tuple(notes)


def _parsed(incoming: Incoming | None, instruction: str, examples: tuple[str, ...], channel_text: str,
            notes: tuple[str, ...]) -> Parsed:
    instruction = instruction.strip()
    sign_off = _SIGN_OFF_AS.search(instruction)
    channel = detect_channel(channel_text) or ("email" if incoming and incoming.is_email else None)
    if incoming is not None and incoming.truncated:
        notes = (*notes, "I read the first 6,000 characters of the message.")
    return Parsed(incoming=incoming if incoming and incoming.text else None, instruction=instruction,
                  examples=examples, channel=channel, has_purpose=has_purpose(instruction),
                  short=bool(_SHORT.search(instruction)), want_subject=bool(_SUBJECT_WANTED.search(instruction)),
                  sign_off_as=sign_off.group(1).strip() if sign_off else "", wants_json=wants_json(instruction),
                  notes=notes)


def has_purpose(instruction: str) -> bool:
    """Whether the instruction says something beyond cue words and extras."""
    rest = _SIGN_OFF_AS.sub(" ", _JSON.sub(" ", instruction or ""))
    words = re.findall(r"[a-z']+", rest.casefold())
    return any(w not in _FILLER for w in words)


def wants_json(text: str) -> bool:
    return bool(_JSON.search(text or ""))
