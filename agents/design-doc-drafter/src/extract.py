"""Requirement extraction: one model call per chunk splits the text; code assigns ids R1..Rn, keeps existing
ids (REQ-3, FR-2) as aliases, verifies every snippet verbatim in the input, merges duplicates and caps the
list. A requirement whose snippet is not in the input is dropped and counted."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import prompts
from grounding import normalise, snippet_in
from llm import TurnDeps, complete_json, off_topic

MAX_REQUIREMENTS = 40
CHUNK_CHARS = 20_000
MAX_CHUNKS = 2
TYPES = ("functional", "nonfunctional", "constraint", "goal", "non_goal")
TITLE_MAX_WORDS = 8

_EXISTING_ID = re.compile(r"\b(?:REQ|FR|NFR|US|R|BR|SR)-?\d{1,4}\b")


@dataclass(frozen=True)
class Extracted:
    status: str                         # ok | off_topic
    title: str = ""
    requirements: tuple[dict, ...] = ()
    dropped: int = 0
    skipped_chars: int = 0


def chunks(text: str) -> tuple[list[str], int]:
    """(up to MAX_CHUNKS chunks cut on line boundaries, characters left unread)."""
    out: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > CHUNK_CHARS and current:
            out.append(current)
            current = ""
        current += line
    if current:
        out.append(current)
    kept = out[:MAX_CHUNKS]
    return kept, sum(len(c) for c in out[MAX_CHUNKS:])


def validate(data: Any) -> dict | None:
    if off_topic(data):
        return {"status": "off_topic"}
    if not isinstance(data, dict) or not isinstance(data.get("requirements"), list):
        return None
    reqs = [r for r in data["requirements"] if isinstance(r, dict) and str(r.get("text") or "").strip()]
    return {"status": "ok", "title": str(data.get("title") or ""), "requirements": reqs}


async def extract(deps: TurnDeps, text: str, policy: Sequence[str], start: int = 1) -> Extracted:
    parts, skipped = chunks(text)
    answers = await asyncio.gather(*(complete_json(deps, prompts.extract_messages(p, policy),
                                                   prompts.EXTRACT_CONTRACT, validate) for p in parts))
    if all(a["status"] == "off_topic" for a in answers):
        return Extracted("off_topic")
    raw = [r for a in answers if a["status"] == "ok" for r in a["requirements"]]
    reqs, dropped = number(raw, text, start)
    title = next((a["title"] for a in answers if a.get("title")), "")
    return Extracted("ok", clean_title(title), tuple(reqs), dropped, skipped)


def number(raw: Sequence[dict], source: str, start: int = 1) -> tuple[list[dict], int]:
    """Verified, deduplicated, capped requirements with ids from `start`; and how many were dropped."""
    ground = normalise(source)
    seen: set[str] = set()
    kept: list[dict] = []
    dropped = 0
    for item in raw:
        text = " ".join(str(item.get("text") or "").split())
        snippet = " ".join(str(item.get("snippet") or "").split())
        key = normalise(text)
        if not snippet_in(snippet, ground):
            dropped += 1
            continue
        if key in seen or len(kept) >= MAX_REQUIREMENTS:
            continue
        seen.add(key)
        kind = str(item.get("type") or "").casefold().replace("-", "_").replace(" ", "_")
        kept.append({"id": f"R{start + len(kept)}", "type": kind if kind in TYPES else "functional",
                     "text": text, "snippet": snippet, "aliases": aliases(text, snippet, source)})
    return kept, dropped


def aliases(text: str, snippet: str, source: str) -> list[str]:
    """Existing ids the user gave this requirement: in its text or at the start of its source line."""
    found = _EXISTING_ID.findall(text)
    norm_snippet = normalise(snippet)
    for line in source.splitlines():
        if norm_snippet and norm_snippet in normalise(line):
            head = _EXISTING_ID.match(line.strip(" -*\t"))
            if head:
                found.append(head.group(0))
            break
    return list(dict.fromkeys(found))


def clean_title(title: str) -> str:
    words = " ".join(str(title or "").split()).strip(" .:#").split()
    return " ".join(words[:TITLE_MAX_WORDS])
