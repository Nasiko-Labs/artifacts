"""Pure: what the text around the table says: the metric's name (and whether the user called it a rate),
a `min_n` for significance, and the events listed as lines. The rest is the user's request, passed to the
model fenced, as data."""

from __future__ import annotations

import re
from dataclasses import dataclass

import events as ev
import stats
from frame import Frame

INSTRUCTION_MAX_CHARS = 500
MIN_N_BOUNDS = (1, 1_000_000)
_METRIC_LABEL = re.compile(r"\bmetric\s*[:=]\s*([^\n.;,]{2,60})", re.IGNORECASE)
_METRIC_MOVED = re.compile(r"^\s*(?:(?:my|our|the|why did|why has|why have)\s+)?([a-z][a-z0-9 %/&'-]{1,48}?)\s+"
                           r"(?:dropped|fell|declined|decreased|rose|grew|increased|jumped|spiked|tanked|went up|"
                           r"went down|is down|is up|changed|moved|dipped|drop|fall|rise)\b", re.IGNORECASE)
_METRIC_BY = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 %/&'-]{1,40}?)\s+by\s+[a-z]", re.IGNORECASE)
_NOT_A_METRIC = frozenset({"here", "this", "these", "table", "data", "breakdown", "before", "after", "it", "what",
                           "split", "numbers", "below", "attached", "is", "are"})
_MIN_N = re.compile(r"\bmin[_ ]?n\s*(?:[:=]|of|is)?\s*(\d{1,7})\b", re.IGNORECASE)
_RATE_WORDS = re.compile(r"\b(?:rate|conversion|ratio|percentage|ctr|cvr)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Request:
    instruction: str = ""
    metric: str = ""            # as the user named it, or ""
    says_rate: bool = False
    min_n: int = stats.DEFAULT_MIN_N
    min_n_given: bool = False
    events: tuple[ev.Event, ...] = ()

    def to_dict(self) -> dict:
        return {"instruction": self.instruction, "metric": self.metric, "says_rate": self.says_rate,
                "min_n": self.min_n, "min_n_given": self.min_n_given}

    @staticmethod
    def from_dict(data: dict | None) -> Request:
        data = data or {}
        return Request(str(data.get("instruction") or ""), str(data.get("metric") or ""),
                       bool(data.get("says_rate")), int(data.get("min_n") or stats.DEFAULT_MIN_N),
                       bool(data.get("min_n_given")))


def parse(text: str, year: int | None = None, first_event: int = 1) -> Request:
    body = (text or "").strip()
    events = tuple(ev.parse(body, first_event, year))
    rest = "\n".join(line for line in body.splitlines() if not ev.parse(line, 1, year))
    metric = _metric(rest)
    min_n_match = _MIN_N.search(rest)
    min_n = stats.DEFAULT_MIN_N
    if min_n_match:
        min_n = min(max(int(min_n_match.group(1)), MIN_N_BOUNDS[0]), MIN_N_BOUNDS[1])
    return Request(rest.strip()[:INSTRUCTION_MAX_CHARS], metric, bool(_RATE_WORDS.search(rest)), min_n,
                   bool(min_n_match), events)


def _metric(text: str) -> str:
    labelled = _METRIC_LABEL.search(text)
    if labelled:
        return " ".join(labelled.group(1).split())
    for line in text.splitlines():
        moved = _METRIC_MOVED.search(line)
        found = moved or _METRIC_BY.search(line)
        name = " ".join(found.group(1).split()) if found else ""
        if name and not set(re.split(r"[^a-z]+", name.casefold())) & _NOT_A_METRIC:
            return name
    return ""


def metric_label(request: Request, frame: Frame) -> str:
    """The metric's name for the reply: the user's words, else from the columns."""
    if request.metric:
        return request.metric
    if frame.kind == "rate":
        num, den = frame.value_cols[0], frame.value_cols[1]
        return f"rate ({_base(num)} / {_base(den)})"
    return _base(frame.value_cols[0]) if frame.layout == "long" else "value"


def _base(column: str) -> str:
    """'signups_before' → 'signups'; 'before_num' → 'num'."""
    words = [w for w in re.split(r"[^A-Za-z0-9]+", column) if w]
    kept = [w for w in words if w.casefold() not in ("before", "after", "prior", "current", "pre", "post", "p1",
                                                       "p2", "baseline", "previous", "now")]
    return " ".join(kept) or column


def type_note(request: Request, frame: Frame) -> str:
    if frame.kind == "rate":
        return f"Read as a rate metric ({_base(frame.value_cols[0])} / {_base(frame.value_cols[1])}, counts given)."
    if request.says_rate:
        return ("Read as an additive metric: you called it a rate, but the table has no numerator and denominator "
                "counts, so I can't split mix from rate or test significance.")
    return "Read as an additive metric (no numerator/denominator columns)."
