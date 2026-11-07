"""The cluster step: one tool-less call groups the distinct labels (the 150 most frequent) into 4-12 themes.
Validated in code: every label exactly once, no unknown label, names of at most 6 words, type from the enum.
An invalid answer gets one repair call; a repair that still fails is salvaged (valid themes kept, every other
label its own theme); no usable answer at all makes each label its own theme."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

import prompts
import records
from budget import TurnLimitReached
from deps import TurnDeps
from reader import ask_once
from records import Item, RawThemes
from themes import customer_key

MAX_NAME_WORDS = 6
MAX_DEFINITION_CHARS = 200
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


@dataclass(frozen=True)
class Clustered:
    themes: tuple[dict, ...]           # {"id", "name", "definition", "type"}
    theme_of_label: dict[str, str]     # label -> theme id
    repaired: bool = False
    fallback: bool = False             # singletons were used for some or all labels
    cut: bool = False


def distinct_labels(items: Sequence[Item], limit: int) -> tuple[list[str], list[str]]:
    """(the most frequent labels up to `limit`, the rest); ties keep first-seen order."""
    counts = Counter(i.label for i in items if i.label)
    ranked = [label for label, _ in counts.most_common()]
    return ranked[:limit], ranked[limit:]


def validate(parsed: RawThemes | None, labels: Sequence[str]) -> tuple[list[dict], str]:
    """(valid themes with their labels, a description of what was wrong or "")."""
    if parsed is None:
        return [], "the answer was not the JSON object asked for"
    known, used, errors, kept = set(labels), set(), [], []
    for theme in parsed.themes:
        name = " ".join(theme.name.split())
        if not name or len(name.split()) > MAX_NAME_WORDS:
            errors.append(f'theme name "{name[:60]}" is over {MAX_NAME_WORDS} words')
            continue
        own = []
        for label in (" ".join(raw.split()).casefold() for raw in theme.labels):
            if label not in known:
                errors.append(f'unknown label "{label[:60]}"')
            elif label in used:
                errors.append(f'label "{label[:60]}" is in more than one theme')
            else:
                used.add(label)
                own.append(label)
        if own:
            kept.append({"name": name, "definition": theme.definition.strip()[:MAX_DEFINITION_CHARS],
                         "type": _type(theme.type), "labels": own})
    missing = [label for label in labels if label not in used]
    if missing:
        errors.append(f"{len(missing)} label(s) missing, e.g. \"{missing[0][:60]}\"")
    return kept, "; ".join(errors[:6])


async def cluster_step(deps: TurnDeps, items: Sequence[Item]) -> Clustered:
    asked, rest = distinct_labels(items, deps.config.max_cluster_labels)
    if not asked:
        return Clustered((), {})
    names = {customer_key(i.customer) for i in items if customer_key(i.customer)}
    try:
        first = records.parse(RawThemes, await ask_once(deps, prompts.cluster_messages(asked)))
        kept, errors = validate(first, asked)
        repaired = bool(errors)
        if errors:
            again = records.parse(RawThemes, await ask_once(deps, prompts.cluster_messages(asked, errors)))
            kept, errors = validate(again, asked) if again is not None else (kept, errors)
    except TurnLimitReached:
        return _finish([], [*asked, *rest], names, repaired=False, cut=True)
    return _finish(kept, [*asked, *rest], names, repaired=repaired, cut=False)


def _finish(kept: list[dict], labels: Sequence[str], names: set[str], *, repaired: bool, cut: bool) -> Clustered:
    covered = {label for theme in kept for label in theme["labels"]}
    singles = [{"name": _single_name(label), "definition": "", "type": "other", "labels": [label]}
               for label in labels if label not in covered]
    themes, theme_of = [], {}
    for n, theme in enumerate(clean_names([*kept, *singles], names), start=1):
        themes.append({k: theme[k] for k in ("name", "definition", "type")} | {"id": f"t{n}"})
        theme_of.update({label: f"t{n}" for label in theme["labels"]})
    return Clustered(tuple(themes), theme_of, repaired, bool(singles), cut)


def clean_names(themes: Sequence[dict], names: set[str]) -> list[dict]:
    """A name holding an email or copying a customer name becomes "Unnamed theme N"; duplicates get a suffix."""
    seen: dict[str, int] = {}
    out = []
    for n, theme in enumerate(themes, start=1):
        name = prompts.strip_markers(theme["name"]).strip()
        lowered = customer_key(name)
        if not name or _EMAIL.search(name) or any(len(k) >= 3 and (k == lowered or re.search(rf"\b{re.escape(k)}\b", lowered))
                                                  for k in names):
            name = f"Unnamed theme {n}"
        key = name.casefold()
        seen[key] = seen.get(key, 0) + 1
        out.append({**theme, "name": name if seen[key] == 1 else f"{name} ({seen[key]})"})
    return out


def _single_name(label: str) -> str:
    words = label.split()[:MAX_NAME_WORDS]
    text = " ".join(words)
    return text[:1].upper() + text[1:]


def _type(raw: str) -> str:
    plain = (raw or "").strip().casefold().replace(" ", "_").replace("-", "_")
    return plain if plain in prompts.THEME_TYPES else "other"
