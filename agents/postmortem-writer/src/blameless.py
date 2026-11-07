"""The blameless pass over every model-written string (prose, factor texts, suggestions, questions).

Strings that fail `lint` go to ONE repair call ("describe the system condition, not the person"). A rewrite
is kept only when it passes the lint and adds no number or time. Whatever still fails becomes the neutral
template built from the first event it cites, or is dropped when it cites none."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

import checks
import grounding as g
import lint
import llm
import prompts
from records import Event, Factor
from write import LIST_KEYS, Prose

log = logging.getLogger("postmortem.blameless")


def _strings(prose: Prose, factors: Sequence[Factor]) -> dict[str, tuple[str, tuple[str, ...]]]:
    """key -> (text, cited ids)."""
    found = {"summary": prose.summary, "impact": prose.impact}
    for key in LIST_KEYS:
        found |= {f"{key}.{n}": entry for n, entry in enumerate(prose.lists.get(key, ()))}
    found |= {f"q.{n}": (q, ()) for n, q in enumerate(prose.questions)}
    found |= {f"suggest.{n}": (a.title, ()) for n, a in enumerate(prose.suggested)}
    found |= {f"factor.{f.id}": (f.text, f.event_ids) for f in factors}
    return {k: v for k, v in found.items() if v[0]}


def parse_lint(data: Any) -> dict | None:
    items = data.get("items") if isinstance(data, Mapping) else None
    return {"items": {str(k): str(v) for k, v in items.items()}} if isinstance(items, Mapping) else None


async def apply(deps: llm.TurnDeps, prose: Prose, factors: tuple[Factor, ...], events: Sequence[Event],
                names: Sequence[str], haystack: str) -> tuple[Prose, tuple[Factor, ...], int]:
    """(prose, factors, rewritten count) with every string blameless."""
    failing = {k: v for k, v in _strings(prose, factors).items() if not lint.is_blameless(v[0], names)}
    if not failing:
        return prose, factors, 0
    rewrites = await _repair(deps, {k: v[0] for k, v in failing.items()})
    norm = g.normalise(haystack)
    by_id = {e.id: e for e in events}
    by_id |= {f.id: by_id[f.event_ids[0]] for f in factors if f.event_ids and f.event_ids[0] in by_id}
    fixed: dict[str, str] = {}
    for key, (text, ids) in failing.items():
        candidate = checks.clean_text(rewrites.get(key), haystack)
        if candidate and lint.is_blameless(candidate, names) and not g.ungrounded_tokens(candidate, norm):
            fixed[key] = candidate
            continue
        cited = next((by_id[i] for i in ids if i in by_id), None)
        fixed[key] = lint.neutral(cited.time_text, cited.text, cited.category, names) if cited else ""
    return _rebuild(prose, factors, fixed), _factors(factors, fixed), len(fixed)


async def _repair(deps: llm.TurnDeps, texts: Mapping[str, str]) -> Mapping[str, str]:
    if deps.budget.left() <= 0:
        return {}
    try:
        parsed = await llm.complete_json(deps, prompts.lint_messages(json.dumps(dict(texts), ensure_ascii=False)),
                                         prompts.LINT_CONTRACT, parse_lint, llm.WRITE_MAX_TOKENS,
                                         repair=lambda: False)
    except llm.LlmError:
        log.info("blameless repair failed; neutral templates are used")
        return {}
    return parsed["items"]


def _swap(entry: tuple[str, tuple[str, ...]], key: str, fixed: Mapping[str, str]) -> tuple[str, tuple[str, ...]]:
    return (fixed[key], entry[1]) if key in fixed else entry


def _rebuild(prose: Prose, factors: tuple[Factor, ...], fixed: Mapping[str, str]) -> Prose:
    lists = {}
    for key in LIST_KEYS:
        entries = (_swap(e, f"{key}.{n}", fixed) for n, e in enumerate(prose.lists.get(key, ())))
        lists[key] = tuple(e for e in entries if e[0])
    questions = tuple(fixed.get(f"q.{n}", q) for n, q in enumerate(prose.questions))
    suggested = tuple(replace(a, title=fixed.get(f"suggest.{n}", a.title)) for n, a in enumerate(prose.suggested))
    return replace(prose, summary=_swap(prose.summary, "summary", fixed), impact=_swap(prose.impact, "impact", fixed),
                   lists=lists, questions=tuple(q for q in questions if q),
                   suggested=tuple(a for a in suggested if a.title))


def _factors(factors: tuple[Factor, ...], fixed: Mapping[str, str]) -> tuple[Factor, ...]:
    out = (replace(f, text=fixed.get(f"factor.{f.id}", f.text)) for f in factors)
    return tuple(f for f in out if f.text)
