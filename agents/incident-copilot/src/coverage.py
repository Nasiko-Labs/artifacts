"""Pure: per-source status and the one line the card shows for each source that did not fully work.
Decided from the offered tools, the tools dropped as unavailable and how each loop ended, never from the
model's prose."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Literal

SOURCES = ("alerting", "metrics", "logs", "errors", "code", "history", "document")
State = Literal["ok", "empty", "missing", "unavailable", "timed_out", "failed", "skipped"]

_LABEL = {"alerting": "Alerting", "metrics": "Metrics", "logs": "Log search", "errors": "Error tracking",
          "code": "The code host", "history": "Past-incident search",
          "document": "Document search"}
_NOUN = {"alerting": "alerting", "metrics": "metrics", "logs": "log search", "errors": "error tracking",
         "code": "code host", "history": "past-incident",
         "document": "document"}
_EFFECT = {
    "alerting": "the start time is the one you gave",
    "metrics": "the timeline is built from deploys and errors only",
    "logs": "log spikes are not included",
    "errors": "error spikes are not included",
    "code": "no deploys or merged PRs are scored",
    "history": "similar past incidents are not included",
    "document": "the runbook comes only from the incident record",
}


@dataclass(frozen=True)
class SourceStatus:
    source: str
    state: State
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping) -> SourceStatus:
        return SourceStatus(str(data.get("source")), data.get("state") or "ok", str(data.get("reason") or ""))


def assess(source: str, offered: Sequence[str], dropped: Mapping[str, str], *, timed_out: float | None = None,
           failed: str = "", found_any: bool = True) -> SourceStatus:
    """`offered`: the tool names the loop could use (helpers excluded)."""
    if not offered:
        return SourceStatus(source, "missing")
    if all(name in dropped for name in offered):
        return SourceStatus(source, "unavailable", ", ".join(dict.fromkeys(dropped[n] for n in offered)))
    if timed_out is not None:
        return SourceStatus(source, "timed_out", f"{round(timed_out)}")
    if failed:
        return SourceStatus(source, "failed", failed)
    return SourceStatus(source, "ok" if found_any else "empty")


def line(status: SourceStatus) -> str:
    label, effect = _LABEL[status.source], _EFFECT[status.source]
    if status.state == "missing":
        if status.source == "alerting":
            return "No alerting source connected: start time is the one you gave."
        return f"No {_NOUN[status.source]} source is connected: {effect}."
    if status.state == "unavailable":
        return f"{label} isn't available for your connection ({status.reason}); {effect}."
    if status.state == "timed_out":
        return f"{label} timed out after {status.reason} s; {effect}."
    if status.state == "failed":
        return f"{label} failed ({status.reason}); {effect}."
    if status.state == "empty":
        return f"{label} returned nothing usable for this window."
    if status.state == "skipped":
        return status.reason
    return ""


def lines(statuses: Sequence[SourceStatus]) -> tuple[str, ...]:
    return tuple(text for text in (line(s) for s in statuses) if text)
