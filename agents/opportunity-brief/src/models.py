"""Pure: verified themes into the output envelope. Strength, score, confidence and needs_human are computed
here from the run's checks, never taken from the model. Links come only from stored items."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import metrics
import scoring
from cluster import Cluster
from store import SOURCE_LABEL, Item
from verify import Checked

MIN_VERIFIED = 2
HIGH_MIN_THEMES = 3
HIGH_MIN_AVG_EVIDENCE = 3
LOW_MAX_DROPPED = 0.30


@dataclass(frozen=True)
class Run:
    """What the turn read: for the summary, coverage and confidence."""

    topic: str
    window_days: int
    items: int
    sources_read: tuple[str, ...]
    sources_missing: tuple[str, ...]      # coverage lines
    topic_stated: bool = True
    asked_failed: bool = False            # a source the user named explicitly could not be read


def evidence(item: Item, quote: str, now: datetime) -> dict:
    return {"id": item.id, "quote": quote, "url": item.url, "source": item.source,
            "metric": metrics.metric(item, now)}


def themes(checked: Sequence[Checked], clusters: Sequence[tuple[int, Cluster, Sequence[Item]]],
           by_id: Mapping[str, Item], window_days: int, now: datetime) -> tuple[list[dict], list[str]]:
    """(themes ordered by score with strength labels, weak-signal labels)."""
    kept: list[tuple[float, int, dict]] = []
    weak: list[str] = []
    for c, (_, theme, _) in zip(checked, clusters, strict=True):
        verified = [by_id[i] for i, _ in c.evidence if i in by_id]
        if len(verified) < MIN_VERIFIED:
            weak.append(theme.label)
            continue
        score, sources = scoring.theme_score(verified, window_days, now)
        kept.append((score, sources, {
            "label": theme.label, "kind": theme.kind, "why_now": c.why_now, "opportunity": c.opportunity,
            "risks": list(c.risks), "next_step": c.next_step, "score": score, "sources": sources,
            "evidence": [evidence(by_id[i], q, now) for i, q in c.evidence if i in by_id]}))
    kept.sort(key=lambda k: -k[0])
    labels = scoring.strength_labels([(s, n) for s, n, _ in kept])
    return [{**t, "strength": label} for (_, _, t), label in zip(kept, labels, strict=True)], weak


def dropped_ratio(checked: Sequence[Checked]) -> float:
    cited = sum(c.cited for c in checked)
    kept = sum(len(c.evidence) for c in checked)
    return 0.0 if cited == 0 else max(0.0, (cited - kept) / cited)


def confidence(run: Run, final: Sequence[dict], dropped: float) -> str:
    read = set(run.sources_read)
    if read <= {"pasted"} or len(final) < 2 or dropped > LOW_MAX_DROPPED:
        return "low"
    avg = sum(len(t["evidence"]) for t in final) / len(final)
    if len(read) >= 2 and len(final) >= HIGH_MIN_THEMES and avg >= HIGH_MIN_AVG_EVIDENCE:
        return "high"
    if len(read) >= 2 or len(final) >= HIGH_MIN_THEMES:
        return "medium"
    return "low"


def summary(run: Run, count: int) -> str:
    names = [SOURCE_LABEL[s] for s in ("hn", "code", "pasted") if s in run.sources_read]
    across = " and ".join([", ".join(names[:-1]), names[-1]] if len(names) > 1 else names) or "no source"
    noun = "theme" if count == 1 else "themes"
    return f"{count} {noun} from {run.items} items across {across}, last {run.window_days} days"


def envelope(run: Run, final: Sequence[dict], weak: Sequence[str], dropped: float) -> dict:
    level = confidence(run, final, dropped)
    return {"summary": summary(run, len(final)), "items": list(final), "confidence": level,
            "needs_human": level == "low" or not run.topic_stated or run.asked_failed,
            "topic": run.topic, "window_days": run.window_days, "items_read": run.items, "sources_read": list(run.sources_read),
            "sources_missing": list(run.sources_missing), "weak_signals": list(weak)}
