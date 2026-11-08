"""Theme grouping. One call per app over its distinct complaint labels (most frequent first, at most
`max_labels`); a competitor's labels are folded into our theme names where the model assigns them. Code
validates: unknown labels are dropped, a label is used once, and any label left out is its own theme."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ValidationError

import checks
import prompts
from deps import TurnDeps
from llm import LlmError, complete_json

log = logging.getLogger("review_miner.cluster")


class _Theme(BaseModel):
    name: str
    labels: list[str]


class _Answer(BaseModel):
    themes: list[_Theme]


def _parse(raw: object) -> _Answer | None:
    try:
        return _Answer.model_validate(raw)
    except ValidationError:
        return None


def assign(themes: Sequence[_Theme], labels: Sequence[str], known: Sequence[str] = ()) -> dict[str, str]:
    """label → theme name. Every label once; unknown labels dropped; leftovers are their own theme. A theme
    name with a number its labels do not carry falls back to its first label; a `known` name is kept."""
    names = {checks.norm(n): n for n in known}
    wanted = {checks.norm(label): label for label in labels}
    out: dict[str, str] = {}
    for theme in themes:
        members = [wanted[checks.norm(x)] for x in theme.labels if checks.norm(x) in wanted]
        members = [m for m in dict.fromkeys(members) if m not in out]
        if not members:
            continue
        name = names.get(checks.norm(theme.name)) or checks.clean_label(theme.name, " ".join(members)) or members[0]
        out.update({m: name for m in members})
    return {label: out.get(label, label) for label in labels}


async def _ask(deps: TurnDeps, system: str, labels: Sequence[str], known: Sequence[str] = ()) -> dict[str, str]:
    if not labels:
        return {}
    messages = ({"role": "system", "content": system}, {"role": "user", "content": prompts.fence_labels(labels)})
    try:
        answer = await complete_json(deps, messages, _parse)
    except LlmError as e:
        log.warning("grouping fell back to one theme per label: %s", e)
        return {label: label for label in labels}
    return assign(answer.themes, labels, known)


def capped(label_counts: Mapping[str, int], cap: int) -> tuple[list[str], list[str]]:
    ordered = sorted(label_counts, key=lambda label: (-label_counts[label], label))
    return ordered[:cap], ordered[cap:]


async def cluster(deps: TurnDeps, label_counts: Mapping[str, int]) -> dict[str, str]:
    sent, rest = capped(label_counts, deps.config.max_labels)
    grouped = await _ask(deps, prompts.CLUSTER_SYSTEM, sent)
    return {**grouped, **{label: label for label in rest}}


async def fold(deps: TurnDeps, our_themes: Sequence[str], label_counts: Mapping[str, int]) -> dict[str, str]:
    """A competitor's labels → our theme names (exact, case-insensitive) or their own new theme names."""
    sent, rest = capped(label_counts, deps.config.max_labels)
    grouped = await _ask(deps, prompts.fold_system(our_themes), sent, our_themes)
    return {**grouped, **{label: label for label in rest}}
