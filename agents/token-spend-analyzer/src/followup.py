"""Pure: follow-ups on a stored analysis, parsed in code with zero model calls: views ("by model", "by team",
"waste only", "recommendations", "as json"), "top 5" (recomputed), the what-if swap, and "cost of team X" (a
filter on the stored frame naming a value present in the export)."""

from __future__ import annotations

import re
from dataclasses import dataclass

import fmt
import whatif
from frame import Frame

COMMAND_MAX_WORDS = 14
MIN_VALUE_CHARS = 2
TOP_MAX = 50


@dataclass(frozen=True)
class Command:
    kind: str                   # view | top | whatif
    view: str = "default"
    top: int | None = None
    ask: whatif.Ask | None = None


_VIEWS = (
    ("model", re.compile(r"\b(?:by|per)\s+models?\b|\bmodels?\s+(?:breakdown|split|view)\b")),
    ("team", re.compile(r"\b(?:by|per)\s+(?:teams?|agents?|projects?|workspaces?)\b")),
    ("task", re.compile(r"\b(?:by|per)\s+(?:tasks?|features?|use cases?)\b")),
    ("recommendations", re.compile(r"\brecommendations?\b|\brecs\b")),
    ("waste", re.compile(r"\bwast(?:e|ing|ed)\b|\bfindings?\b|\bsavings?\b|\bretries\b|\boversized\b")),
    ("default", re.compile(r"^(?:as json|json only|in json|full report|summary|everything|show all|all|again)$")),
)
_TOP = re.compile(r"\btop\s+(\d{1,2})\b")
_COST_Q = re.compile(r"\b(?:cost|costs|spend|spent|spending|how much)\b")
_FILLER = re.compile(r"\b(?:only|just|show|me|the|please|give|as json|json only|in json)\b")


def plain(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9\-. ]+", " ", (text or "").casefold()).split())


def parse(text: str) -> Command | None:
    ask = whatif.parse(text)
    if ask is not None:
        return Command("whatif", ask=ask)
    p = plain(text)
    if not p or len(p.split()) > COMMAND_MAX_WORDS:
        return None
    top = _TOP.search(p)
    view = _view_of(p)
    if top:
        return Command("top", view or "default", top=max(1, min(int(top.group(1)), TOP_MAX)))
    return Command("view", view) if view else None


def _view_of(p: str) -> str | None:
    stripped = " ".join(_FILLER.sub(" ", p).split())
    for view, pattern in _VIEWS:
        if pattern.search(stripped or p):
            return view
    return None


def cost_query(text: str, frame: Frame, team_label: str = "team") -> str | None:
    """"Cost of team support": totals over rows whose team, task or model is named in the message."""
    low = (text or "").casefold()
    if not _COST_Q.search(low):
        return None
    df = frame.df
    for dim in ("team", "task", "model"):
        if dim not in frame.dims:
            continue
        values = [v for v in df[dim].dropna().astype(str).unique() if len(v) >= MIN_VALUE_CHARS]
        hits = sorted((v for v in values if re.search(rf"(?<![\w-]){re.escape(v.casefold())}(?![\w-])", low)),
                      key=len, reverse=True)
        if not hits:
            continue
        rows = df[df[dim].astype(str) == hits[0]]
        total = float(df["cost"].sum())
        cost = float(rows["cost"].sum())
        label = team_label if dim == "team" else dim
        line = (f"**{label} `{fmt.safe(hits[0])}`:** {fmt.money(cost)} "
                f"({fmt.pct(cost / total * 100 if total else None)} of cost), {fmt.count(rows['requests'].sum())} "
                f"requests, {fmt.count(rows['input'].sum())} input / {fmt.count(rows['output'].sum())} output tokens.")
        if dim != "model" and "model" in frame.dims:
            by_model = rows.groupby("model")["cost"].sum().sort_values(ascending=False).head(3)
            line += " Top models: " + ", ".join(f"{fmt.safe(m)} {fmt.money(c)}" for m, c in by_model.items()) + "."
        return line + "\n_(Filtered in code from the stored export.)_"
    return None
