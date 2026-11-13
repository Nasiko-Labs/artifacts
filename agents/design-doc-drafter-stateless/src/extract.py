"""Pure: reading the requirements analyst's answer. Code assigns ids R1..Rn, keeps existing ids (REQ-3, FR-2)
as aliases, verifies every snippet verbatim in the input, merges duplicates and caps the list. A requirement
whose snippet is not in the input is dropped and counted. The analyst's own ids (which the architect and the
writer cite) map onto the final ids; a dropped requirement's id maps to nothing, a duplicate's to the kept one."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from grounding import normalise, snippet_in
from llm import off_topic

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
    id_map: dict | None = None          # the analyst's id (upper case) -> final id, or "" when dropped


def readable(text: str) -> tuple[str, int]:
    """(the text the analyst reads: up to MAX_CHUNKS chunks cut on line boundaries, characters left unread)."""
    kept, skipped = chunks(text)
    return "".join(kept), skipped


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


def read(data: Any, text: str, skipped: int = 0) -> Extracted | None:
    """The analyst's answer, checked against `text`; None when it does not match the contract."""
    answer = validate(data)
    if answer is None:
        return None
    if answer["status"] == "off_topic":
        return Extracted("off_topic")
    reqs, dropped, id_map = number(answer["requirements"], text)
    return Extracted("ok", clean_title(answer["title"]), tuple(reqs), dropped, skipped, id_map)


def number(raw: Sequence[dict], source: str, start: int = 1) -> tuple[list[dict], int, dict]:
    """Verified, deduplicated, capped requirements with ids from `start`; how many were dropped; the id map."""
    ground = normalise(source)
    seen: dict[str, str] = {}
    kept: list[dict] = []
    dropped = 0
    id_map: dict[str, str] = {}
    for n, item in enumerate(raw, start=1):
        own = str(item.get("id") or f"R{n}").strip().upper()
        text = " ".join(str(item.get("text") or "").split())
        snippet = " ".join(str(item.get("snippet") or "").split())
        key = normalise(text)
        if not snippet_in(snippet, ground):
            dropped += 1
            id_map.setdefault(own, "")
            continue
        if key in seen or len(kept) >= MAX_REQUIREMENTS:
            id_map.setdefault(own, seen.get(key, ""))
            continue
        seen[key] = f"R{start + len(kept)}"
        id_map.setdefault(own, seen[key])
        kind = str(item.get("type") or "").casefold().replace("-", "_").replace(" ", "_")
        kept.append({"id": f"R{start + len(kept)}", "type": kind if kind in TYPES else "functional",
                     "text": text, "snippet": snippet, "aliases": aliases(text, snippet, source)})
    return kept, dropped, id_map


def remap(value: Any, id_map: dict) -> Any:
    """`satisfies` / `verifies` lists rewritten from the analyst's ids to the final ones (unknown ids kept, so
    the cleaners drop them as before)."""
    if isinstance(value, dict):
        return {k: ([id_map.get(str(i).strip().upper(), i) for i in v] if k in ("satisfies", "verifies")
                    and isinstance(v, list) else remap(v, id_map)) for k, v in value.items()}
    if isinstance(value, list):
        return [remap(v, id_map) for v in value]
    return value


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
