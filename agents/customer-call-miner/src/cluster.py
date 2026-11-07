"""The cluster step: one tool-less LLM call per kind (feature requests, objections) over the DISTINCT
labels only; code validates the grouping. Competitors are grouped in code by name; churn signals are
not clustered."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

import prompts
from checks import normalise
from deps import TurnDeps
from reader import ask_once
from records import Item, load_json_object, text_of

log = logging.getLogger("call_miner.cluster")

CLUSTERED_KINDS = ("feature_request", "objection")
Grouping = dict[str, str]  # normalised label → theme name


def distinct_labels(items: Sequence[Item], kind: str, arr: Mapping[str, float]) -> list[str]:
    """Each label once (first spelling), ordered by the highest ARR behind it, so a cap keeps the biggest."""
    best: dict[str, tuple[float, str]] = {}
    for item in items:
        if item.type != kind:
            continue
        key = normalise(item.label)
        value = arr.get(item.account_id or "", 0.0)
        if key not in best or value > best[key][0]:
            best[key] = (value, best.get(key, (0.0, item.label))[1])
    return [label for _, label in sorted(best.values(), key=lambda pair: -pair[0])]


def validate(labels: Sequence[str], data: object) -> Grouping:
    """Every input label assigned exactly once: missing → its own theme, duplicates → first wins,
    unknown labels ignored."""
    wanted = {normalise(label): label for label in labels}
    grouping: Grouping = {}
    themes = data.get("themes") if isinstance(data, Mapping) else None
    for theme in themes if isinstance(themes, list) else ():
        if not isinstance(theme, Mapping) or not isinstance(theme.get("labels"), list):
            continue
        name = " ".join(text_of(theme.get("name")).split()[:6])
        for label in theme["labels"]:
            key = normalise(text_of(label))
            if key in wanted and key not in grouping:
                grouping[key] = name or wanted[key]
    for key, label in wanted.items():
        grouping.setdefault(key, label)
    return grouping


async def cluster(deps: TurnDeps, items: Sequence[Item], arr: Mapping[str, float]) -> dict[str, Grouping]:
    groupings: dict[str, Grouping] = {}
    for kind in CLUSTERED_KINDS:
        labels = distinct_labels(items, kind, arr)
        sent, rest = labels[:deps.config.max_labels], labels[deps.config.max_labels:]
        grouping = await _ask(deps, kind, sent) if len(sent) > 1 else validate(sent, None)
        groupings[kind] = {**grouping, **{normalise(label): label for label in rest}}
    return groupings


async def _ask(deps: TurnDeps, kind: str, labels: Sequence[str]) -> Grouping:
    messages = ({"role": "system", "content": prompts.cluster_system(kind)},
                {"role": "user", "content": prompts.fence_labels("\n".join(labels))})
    try:
        data = load_json_object(await ask_once(deps, messages))
    except Exception as e:  # noqa: BLE001 - without grouping every label is its own theme
        log.warning("cluster call failed: %s", type(e).__name__)
        data = None
    return validate(labels, data)
