"""Pure: follow-ups on a stored analysis, parsed in code. Re-render views and recomputes ("top 10 movers",
"compare last 14 days") cost zero LLM calls; "explain it" / "why …" re-narrates (one call); a cost question
naming values present in the export ("cost of EC2 in us-east-1") is a code filter on the stored frame."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd

from digest import money
from frame import Frame
from stats import gross
from windows import user_window

COMMAND_MAX_WORDS = 12
MIN_VALUE_CHARS = 2


@dataclass(frozen=True)
class Command:
    kind: str                   # view | recompute | explain | cost
    view: str = "default"
    top: int | None = None
    window_days: int | None = None


_VIEWS = (
    ("sources", re.compile(r"\b(?:sources?|evidence|drivers?|citations?)\b")),
    ("anomalies", re.compile(r"\banomal(?:y|ies)\b|\bspikes?\b")),
    ("untagged", re.compile(r"\buntagged\b|\btag(?:s|ging)?\b")),
    ("waste", re.compile(r"\bwaste\b|\bsavings?\b")),
    ("recommendations", re.compile(r"\brecommendations?\b|\brecs?\b")),
    ("chart", re.compile(r"\bchart\b|\bgraph\b|\bpng\b|\bsparklines?\b")),
    ("movers", re.compile(r"\bmovers?\b|\bhigh-risk ones\b")),
    ("default", re.compile(r"^(?:as json|json only|in json|full report|summary|everything|show all|all)$")),
)
_TOP = re.compile(r"\btop\s+(\d{1,2})\b")
_COMPARE = re.compile(r"\b(?:compare|vs|versus|window|last|past)\b")
_EXPLAIN = re.compile(r"^(?:explain|why\b|what (?:drove|caused|explains)|narrat)")
_COST_Q = re.compile(r"\b(?:cost|spend|spent|how much)\b")
_PLAIN = re.compile(r"[^a-z0-9\-. ]+")


def plain(text: str) -> str:
    return " ".join(_PLAIN.sub(" ", (text or "").casefold()).split())


def parse(text: str) -> Command | None:
    p = plain(text)
    if not p or len(p.split()) > COMMAND_MAX_WORDS:
        return None
    if _EXPLAIN.match(p):
        return Command("explain")
    days = user_window(p) if _COMPARE.search(p) else None
    top = _TOP.search(p)
    if days or top:
        return Command("recompute", _view_of(p) or "default", top=int(top.group(1)) if top else None,
                       window_days=days)
    view = _view_of(p)
    return Command("view", view) if view else None


def _view_of(p: str) -> str | None:
    stripped = re.sub(r"\b(?:only|just|show|me|the|please|with|give|as json|json only|in json)\b", " ", p).strip()
    for view, pattern in _VIEWS:
        if pattern.search(stripped or p):
            return view
    return None


def cost_query(text: str, frame: Frame) -> str | None:
    """"What is the cost of EC2 in us-east-1": the gross sum over rows matching every value named. None when
    the message names no value present in the export's dimensions."""
    p = (text or "").casefold()
    if not _COST_Q.search(p):
        return None
    g = gross(frame.df)
    mask = pd.Series(True, index=g.index)
    named: list[str] = []
    for column in ("service", *frame.dims()):
        values = [v for v in g[column].dropna().astype(str).unique() if len(v) >= MIN_VALUE_CHARS]
        hits = [v for v in values if re.search(rf"(?<![\w-]){re.escape(v.casefold())}(?![\w-])", p)]
        if not hits:
            hits = [v for v in values if column == "service" and _short_name_hit(v, p)]
        if hits:
            mask &= g[column].astype(str).isin(hits)
            named.append(" / ".join(hits))
    if not named:
        return None
    rows = g[mask]
    if rows.empty:
        return f"No spend in this export matches {', '.join(named)}."
    start, end = rows["date"].min().strftime("%Y-%m-%d"), rows["date"].max().strftime("%Y-%m-%d")
    return (f"**{' in '.join(named)}:** {money(float(rows['cost'].sum()), frame.currency)} gross from {start} to "
            f"{end} ({len(rows):,} rows of the export).")


def _short_name_hit(value: str, p: str) -> bool:
    """"EC2" names "AmazonEC2": a word of the question that is a long-enough tail of the service name."""
    low = value.casefold()
    return any(len(w) >= 2 and low.endswith(w) and len(w) >= len(low) // 3
               for w in re.findall(r"[a-z0-9]+", p) if w not in {"cost", "of", "in", "the", "is", "what"})
