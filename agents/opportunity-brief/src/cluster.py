"""Clustering: one LLM call groups the stored items into 3-5 themes (ids only); code validates every id,
keeps each item in one theme, and keeps a one-item theme only when that item's signal is top quartile."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import prompts
import scoring
from deps import TurnDeps
from llm import call_json
from store import Item

MAX_THEMES = 5
CLUSTER_TEXT_CHARS = 200
MAX_LABEL_CHARS = 80
KINDS = ("pain", "launch", "shift", "gap")
DEFAULT_KIND = "gap"


@dataclass(frozen=True)
class Cluster:
    label: str
    kind: str
    item_ids: tuple[str, ...]

    def to_dict(self) -> dict:
        return {"label": self.label, "kind": self.kind, "item_ids": list(self.item_ids)}

    @staticmethod
    def from_dict(data: Mapping) -> Cluster:
        return Cluster(str(data["label"]), str(data["kind"]), tuple(str(i) for i in data.get("item_ids") or ()))


def cluster_user(topic: str, items: Sequence[Item]) -> str:
    blocks = [prompts.fence(i.id, i.text[:CLUSTER_TEXT_CHARS]) for i in items]
    return (f"TOPIC: {prompts.strip_markers(topic)}\nTHEMES: at most {theme_cap(len(items))}\n\nEVIDENCE ITEMS:\n"
            + "\n".join(blocks))


def theme_cap(n_items: int) -> int:
    """One theme per two items, 1 to 5: three pasted items make one theme, not three weak ones."""
    return max(1, min(MAX_THEMES, n_items // 2))


async def cluster(deps: TurnDeps, topic: str, items: Sequence[Item], window_days: int) -> list[Cluster]:
    answer = await call_json(deps, prompts.CLUSTER_SYSTEM, cluster_user(topic, items))
    return validate(answer, items, window_days, deps.now)


def validate(answer: Mapping, items: Sequence[Item], window_days: int, now: datetime) -> list[Cluster]:
    known = {i.id: i for i in items}
    signals = {i.id: scoring.weighted(i, window_days, now) for i in items}
    used: set[str] = set()
    out: list[Cluster] = []
    raw = answer.get("themes") if isinstance(answer.get("themes"), list) else []
    for theme in raw:
        if not isinstance(theme, Mapping) or len(out) >= theme_cap(len(items)):
            continue
        label = " ".join(str(theme.get("label") or "").split())[:MAX_LABEL_CHARS]
        ids = tuple(dict.fromkeys(str(i).strip() for i in theme.get("item_ids") or [] if str(i).strip() in known))
        ids = tuple(i for i in ids if i not in used)
        if not label or not ids:
            continue
        if len(ids) == 1 and not scoring.top_quartile(signals[ids[0]], list(signals.values())):
            continue
        kind = str(theme.get("kind") or "").casefold()
        out.append(Cluster(prompts.strip_markers(label), kind if kind in KINDS else DEFAULT_KIND, ids))
        used |= set(ids)
    return out
