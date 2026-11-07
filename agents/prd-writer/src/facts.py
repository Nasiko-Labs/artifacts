"""Facts from the brief: one extract call (or one per ~12k-char chunk, at most three), then code keeps only
facts whose snippet is really in the brief, dedupes them and numbers them F1..Fn."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

import prompts
from grounding import locate, normalise
from llm import TurnDeps, complete_json
from prd import FACT_KINDS, Fact

CHUNK_CHARS = 12_000
MAX_CHUNKS = 3
MAX_FACTS = 60
MAX_TEXT_CHARS = 300
MAX_SNIPPET_CHARS = 240


@dataclass(frozen=True)
class Extracted:
    status: str                       # ok | off_topic
    facts: tuple[Fact, ...] = ()
    dropped: int = 0
    parts: int = 1
    total: int = 1
    covered_chars: int = 0


def chunks(brief: str, size: int = CHUNK_CHARS) -> list[str]:
    """Cut at paragraph, line or sentence boundaries, each chunk <= size."""
    out, rest = [], brief.strip()
    while len(rest) > size:
        head = rest[:size]
        cut = max(head.rfind("\n\n"), head.rfind("\n"), head.rfind(". "))
        cut = cut + 1 if cut >= size // 2 else size
        out.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    return [*out, rest] if rest else out


def _validate(data: object) -> dict | None:
    if not isinstance(data, dict):
        return None
    if data.get("status") == "off_topic":
        return {"status": "off_topic", "facts": []}
    facts = data.get("facts")
    if not isinstance(facts, list):
        return None
    return {"status": "ok", "facts": [f for f in facts if isinstance(f, dict)]}


async def extract(deps: TurnDeps, brief: str, instruction: str, policy: Sequence[str] = ()) -> Extracted:
    pieces = chunks(brief)
    used = pieces[:MAX_CHUNKS]
    if len(used) == 1:
        results = [await complete_json(deps, prompts.extract_messages(used[0], instruction, policy),
                                       prompts.EXTRACT_CONTRACT, _validate)]
    else:
        calls = [complete_json(deps, prompts.extract_messages(p, instruction, policy, (n, len(used))),
                               prompts.EXTRACT_CONTRACT, _validate) for n, p in enumerate(used, start=1)]
        results = await asyncio.gather(*calls)
    if all(r["status"] == "off_topic" for r in results):
        return Extracted("off_topic")
    raw = [f for r in results for f in r["facts"]]
    facts, dropped = verify(raw, brief)
    return Extracted("ok", facts, dropped, len(used), len(pieces), sum(len(p) for p in used))


def verify(raw: Sequence[dict], corpus: str) -> tuple[tuple[Fact, ...], int]:
    """(kept facts numbered F1.., how many were dropped): unknown kind, no snippet, snippet not in the
    corpus, or a duplicate of an earlier snippet."""
    norm = normalise(corpus)
    kept: list[Fact] = []
    seen: set[str] = set()
    dropped = 0
    for item in raw:
        kind = str(item.get("kind") or "").strip().casefold()
        snippet = str(item.get("snippet") or "").strip()[:MAX_SNIPPET_CHARS]
        text = str(item.get("text") or "").strip()[:MAX_TEXT_CHARS] or snippet
        key = normalise(snippet)
        if kind not in FACT_KINDS or not locate(snippet, norm):
            dropped += 1
            continue
        if (kind, key) in seen or len(kept) >= MAX_FACTS:
            continue
        seen.add((kind, key))
        kept.append(Fact(f"F{len(kept) + 1}", kind, prompts.strip_markers(text), snippet))
    return tuple(kept), dropped


def answer_fact(answer: str, facts: tuple[Fact, ...]) -> tuple[Fact, ...]:
    """The user's answer to "Who is this for?" as a grounded `user` fact."""
    text = answer.strip()
    return (*facts, Fact(f"F{len(facts) + 1}", "user", f"For: {text}", text))


def facts_block(facts: Sequence[Fact]) -> str:
    return "\n".join(f'{f.id} [{f.kind}] {f.text} | snippet: "{f.snippet}"' for f in facts)


def has_kind(facts: Sequence[Fact], *kinds: str) -> bool:
    return any(f.kind in kinds for f in facts)

