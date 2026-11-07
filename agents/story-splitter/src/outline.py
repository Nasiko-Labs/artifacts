"""The outline: the epic's capabilities, actors, rules and unknowns, one call per chunk. Code drops every item
whose snippet is not in the epic and gives the survivors ids (c1, a1, r1, u1): this is the grounding set."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import prompts
from grounding import contains, invented, normalize
from llm import LlmError, TurnDeps, complete_json

log = logging.getLogger("story-splitter.outline")

CHUNK_CHARS = 8_000
MAX_ITEMS = {"capabilities": 20, "actors": 8, "rules": 15, "unknowns": 8}
MAX_ITEM_CHARS = 300
OFF_TOPIC = "off_topic"


@dataclass(frozen=True)
class Outline:
    capabilities: tuple[dict, ...] = ()   # {"id", "text", "snippet"}
    actors: tuple[dict, ...] = ()         # {"id", "name", "snippet"}
    rules: tuple[dict, ...] = ()          # {"id", "text", "snippet"}
    unknowns: tuple[dict, ...] = ()       # {"id", "text"}
    dropped: int = 0
    chunks: int = 1
    status: str = "ok"
    extra_actors: tuple[str, ...] = field(default=())  # personas / the user's answer: allowed, not from the epic

    def to_dict(self) -> dict:
        return {"capabilities": list(self.capabilities), "actors": list(self.actors), "rules": list(self.rules),
                "unknowns": list(self.unknowns), "dropped": self.dropped, "chunks": self.chunks,
                "extra_actors": list(self.extra_actors)}

    @staticmethod
    def from_dict(data: Any) -> Outline:
        raw = data if isinstance(data, dict) else {}
        return Outline(*(tuple(d for d in raw.get(k) or () if isinstance(d, dict))
                         for k in ("capabilities", "actors", "rules", "unknowns")),
                       dropped=int(raw.get("dropped") or 0), chunks=int(raw.get("chunks") or 1),
                       extra_actors=tuple(str(a) for a in raw.get("extra_actors") or ()))

    def ids(self) -> set[str]:
        return {i["id"] for group in (self.capabilities, self.actors, self.rules, self.unknowns) for i in group}

    def actor_names(self) -> list[str]:
        return [a["name"] for a in self.actors] + list(self.extra_actors)

    def block(self) -> str:
        """The outline as the slice and resplit prompts see it."""
        lines = ["ACTORS: " + (", ".join(f"{a}" for a in self.actor_names()) or "none named")]
        lines += [f"{c['id']}: {c['text']}" for c in self.capabilities]
        lines += [f"{r['id']}: {r['text']}" for r in self.rules]
        lines += [f"{u['id']}: {u['text']}" for u in self.unknowns]
        return "\n".join(lines)


def chunks(epic: str, size: int = CHUNK_CHARS) -> list[str]:
    """Paragraph-bounded pieces of at most about `size` chars (a single huge paragraph is cut hard)."""
    if len(epic) <= size:
        return [epic]
    pieces: list[str] = []
    current = ""
    for para in epic.split("\n\n"):
        for piece in (para[i:i + size] for i in range(0, max(len(para), 1), size)):
            if current and len(current) + len(piece) + 2 > size:
                pieces.append(current)
                current = ""
            current = f"{current}\n\n{piece}" if current else piece
    return [p for p in [*pieces, current] if p.strip()]


def _validate(data: Any) -> dict | None:
    if not isinstance(data, dict):
        return None
    if data.get("status") == OFF_TOPIC:
        return {"status": OFF_TOPIC}
    return data if isinstance(data.get("capabilities"), list) else None


async def build(deps: TurnDeps, epic: str, constraints: Sequence[str], policy: Sequence[str] = ()) -> Outline:
    """One outline call per chunk (concurrent), merged by snippet. Raises LlmError when a call failed."""
    parts = chunks(epic)
    deps.budget.allow_chunks(len(parts))
    label = (lambda i: f"part {i + 1} of {len(parts)}") if len(parts) > 1 else (lambda i: "")
    calls = [complete_json(deps, prompts.outline_messages(p, constraints, policy, label(i)),
                           prompts.OUTLINE_CONTRACT, _validate) for i, p in enumerate(parts)]
    answers = await asyncio.gather(*calls, return_exceptions=True)
    failed = [a for a in answers if isinstance(a, BaseException)]
    if failed:
        raise LlmError(f"outline failed for {len(failed)} chunk(s)") from failed[0]
    if all(a.get("status") == OFF_TOPIC for a in answers):
        return Outline(status=OFF_TOPIC)
    return merge(answers, epic, constraints, len(parts))


def merge(answers: Sequence[dict], epic: str, constraints: Sequence[str], chunk_count: int = 1) -> Outline:
    ground = normalize(epic)
    full = epic + "\n" + "\n".join(constraints)
    groups: dict[str, list[dict]] = {k: [] for k in MAX_ITEMS}
    seen: set[tuple[str, str]] = set()
    dropped = 0
    for answer in answers:
        for kind, limit in MAX_ITEMS.items():
            for raw in answer.get(kind) or ():
                item = _grounded(kind, raw, ground, full)
                if item is None:
                    dropped += 1
                    continue
                dedupe = (kind, normalize(item.get("snippet") or item.get("text") or item.get("name")))
                if dedupe not in seen and len(groups[kind]) < limit:
                    seen.add(dedupe)
                    groups[kind].append(item)
    prefix = {"capabilities": "c", "actors": "a", "rules": "r", "unknowns": "u"}
    numbered = {k: tuple({**item, "id": f"{prefix[k]}{n}"} for n, item in enumerate(v, start=1))
                for k, v in groups.items()}
    return Outline(numbered["capabilities"], numbered["actors"], numbered["rules"], numbered["unknowns"],
                   dropped=dropped, chunks=chunk_count)


def _grounded(kind: str, raw: Any, ground_norm: str, full: str) -> dict | None:
    """The item when its snippet is in the epic (an unknown: when it invents no fact), else None."""
    item = _item(kind, raw)
    if item is None:
        return None
    if kind == "unknowns":
        return None if invented(item["text"], full) else item
    return item if contains(ground_norm, item["snippet"]) else None


def _item(kind: str, raw: Any) -> dict | None:
    if kind == "unknowns":
        text = raw if isinstance(raw, str) else (raw.get("text") if isinstance(raw, dict) else None)
        text = " ".join(str(text or "").split())[:MAX_ITEM_CHARS]
        return {"text": text} if text else None
    if not isinstance(raw, dict):
        return None
    snippet = " ".join(str(raw.get("snippet") or "").split())[:MAX_ITEM_CHARS]
    key = "name" if kind == "actors" else "text"
    value = " ".join(str(raw.get(key) or raw.get("text") or "").split())[:MAX_ITEM_CHARS]
    if not snippet or not value:
        return None
    if kind == "rules":  # a rule is the epic's own words
        value = snippet
    return {key: value, "snippet": snippet}
