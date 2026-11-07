"""Pure: the model's raw stories become checked `Story` values. Actors must be allowed, `i_want` starts with a
verb, `so_that` is the epic's benefit or a placeholder, basis ids exist, sizes come from facts only.
Duplicates are merged, out-of-scope stories and extra spikes dropped, every change noted."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import sizing
from grounding import coverage, jaccard, normalize, stem
from outline import Outline
from stories import (
    PATTERNS,
    PLACEHOLDER_ACTOR,
    PLACEHOLDER_BENEFIT,
    SPIKE_CRITERION,
    Story,
)

DUPLICATE_JACCARD = 0.8
OUT_OF_SCOPE_COVERAGE = 0.6
BENEFIT_MIN_COVERAGE = 0.5
MAX_TITLE_CHARS = 120
MAX_FIELD_CHARS = 300
_LEADS = ("i want to ", "i want ", "to ", "be able to ", "can ")
_DETERMINERS = frozenset({"a", "an", "the", "this", "that", "some", "all", "each", "every", "my", "our"})


@dataclass(frozen=True)
class Ground:
    epic: str
    constraints: tuple[str, ...] = ()
    outline: Outline = field(default_factory=Outline)

    @property
    def full(self) -> str:
        return "\n".join([self.epic, *self.constraints, *self.outline.extra_actors])


@dataclass
class Report:
    """Counters for the envelope and the notes under the reply (mutable, one per run)."""

    actors_replaced: int = 0
    dropped: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)


def _text(value: Any, limit: int = MAX_FIELD_CHARS) -> str:
    return " ".join(str(value or "").split())[:limit] if isinstance(value, str) else ""


def actor_of(name: str, allowed: list[str]) -> str | None:
    """The allowed actor `name` refers to (plural/case tolerant), or None."""
    wanted = {stem(w) for w in normalize(name).split()}
    for actor in allowed:
        if wanted and wanted == {stem(w) for w in normalize(actor).split()}:
            return actor
    return None


def singular(name: str) -> str:
    """"sales admins" -> "sales admin": the story reads "As a <actor>"."""
    head, _, last = name.strip().rpartition(" ")
    if len(last) > 3 and last.endswith("s") and not last.endswith(("ss", "us", "is")):
        last = last[:-3] + "y" if last.endswith("ies") else last[:-1]
    return f"{head} {last}".strip()


def verb_first(i_want: str, title: str) -> str:
    """`i_want` without "I want to"; if it opens with an article it is rebuilt from the title."""
    text = i_want.strip().rstrip(".")
    lowered = text.casefold()
    for lead in _LEADS:
        if lowered.startswith(lead):
            text, lowered = text[len(lead):], lowered[len(lead):]
    first = lowered.split(" ", 1)[0] if lowered else ""
    if not first or first in _DETERMINERS or not first.isalpha():
        return title[:1].lower() + title[1:] if title else text
    return text


def benefit(so_that: str, ground: Ground) -> str:
    if not so_that or coverage(so_that, ground.full) < BENEFIT_MIN_COVERAGE:
        return PLACEHOLDER_BENEFIT
    return so_that.rstrip(".")


def parse(raw: Any, key: str, keys_by_n: dict[int, str], ground: Ground, report: Report) -> Story | None:
    """One raw story -> a checked Story, or None when it has no title or capability."""
    if not isinstance(raw, dict):
        return None
    title, i_want = _text(raw.get("title"), MAX_TITLE_CHARS), _text(raw.get("i_want"))
    if not title or not i_want:
        return None
    allowed = ground.outline.actor_names()
    actor = actor_of(_text(raw.get("as_a")), allowed) if raw.get("as_a") else None
    actor = singular(actor) if actor else None
    if actor is None:
        actor = PLACEHOLDER_ACTOR
        report.actors_replaced += 1 if raw.get("as_a") else 0
    pattern = raw.get("slice_pattern") if raw.get("slice_pattern") in PATTERNS else "workflow_step"
    ids = ground.outline.ids()
    basis = tuple(dict.fromkeys(b.strip().lower() for b in raw.get("basis") or ()
                                if isinstance(b, str) and b.strip().lower() in ids))
    n = raw.get("n")
    numbers = [int(d) if isinstance(d, str) and d.strip().isdigit() else d for d in raw.get("depends_on") or ()]
    deps = tuple(dict.fromkeys(keys_by_n[d] for d in numbers
                               if isinstance(d, int) and d in keys_by_n and (not isinstance(n, int) or d < n)))
    story = Story(key=key, title=title, as_a=actor, i_want=verb_first(i_want, title),
                  so_that=benefit(_text(raw.get("so_that")), ground), slice_pattern=pattern, basis=basis,
                  depends_on=deps, facts=sizing.Facts.from_dict(raw))
    return replace(story, acceptance=(SPIKE_CRITERION,)) if story.is_spike else story


def parse_all(raws: Any, prefix: str, ground: Ground, report: Report) -> list[Story]:
    items = [r for r in raws or () if isinstance(r, dict)]
    numbers = [r.get("n") if isinstance(r.get("n"), int) else i for i, r in enumerate(items, start=1)]
    keys_by_n = {n: f"{prefix}{i}" for i, n in enumerate(numbers, start=1)}
    stories = [parse({**r, "n": n}, f"{prefix}{i}", keys_by_n, ground, report)
               for i, (r, n) in enumerate(zip(items, numbers, strict=True), start=1)]
    return [s for s in stories if s is not None]


def clean(stories: list[Story], out_of_scope: list[str], report: Report) -> list[Story]:
    """Drops out-of-scope stories and extra spikes, merges duplicates; dependencies follow the survivors."""
    kept: list[Story] = []
    alias: dict[str, str] = {}
    for story in stories:
        text = f"{story.title} {story.i_want}"
        hit = next((item for item in out_of_scope if coverage(item, text) >= OUT_OF_SCOPE_COVERAGE), None)
        if hit is not None:
            report.dropped.append(f"\"{story.title}\" (out of scope: {hit})")
            continue
        if story.is_spike and any(k.is_spike for k in kept):
            report.dropped.append(f"\"{story.title}\" (only one spike per epic)")
            continue
        twin = next((k for k in kept if jaccard(k.i_want, story.i_want) >= DUPLICATE_JACCARD), None)
        if twin is not None:
            alias[story.key] = twin.key
            report.merged.append(f"\"{story.title}\" merged into \"{twin.title}\" (same capability)")
            merged = replace(twin, basis=tuple(dict.fromkeys((*twin.basis, *story.basis))))
            kept = [merged if k.key == twin.key else k for k in kept]
            continue
        kept.append(story)
    return remap(kept, alias)


def remap(stories: list[Story], alias: dict[str, str | tuple[str, ...]]) -> list[Story]:
    """Rewrites dependencies through `alias` (one key -> one or more keys) and drops unknown or self edges."""
    live = {s.key for s in stories}
    result: list[Story] = []
    for story in stories:
        targets: list[str] = []
        for dep in story.depends_on:
            mapped = alias.get(dep, dep)
            targets += [mapped] if isinstance(mapped, str) else list(mapped)
        deps = tuple(dict.fromkeys(t for t in targets if t in live and t != story.key))
        result.append(replace(story, depends_on=deps))
    return result
