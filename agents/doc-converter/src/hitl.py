"""The two questions this agent can pause on (one per pause), what to resume from, and answer reading.

Q1 "Where is the file?" when files are named but not where they live. Q2 "Which file?" when a name
matches several files. The job is kept on the A2A task's metadata, so any replica can resume it."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import sources
from listing import Entry
from nasiko_hitl import question_metadata

PENDING_KEY = "doc_converter_pending"
Q1_HEADER = "Where is the file?"
Q2_HEADER = "Which file?"


@dataclass(frozen=True)
class Job:
    place: str | None = None
    requests: tuple[dict, ...] = ()   # still to locate: {"query", "link"}
    resolved: tuple[dict, ...] = ()   # Entry dicts, located
    failed: tuple[dict, ...] = ()     # Failure dicts
    wants_json: bool = False
    pages: tuple[int, int] | None = None
    sheet: str | None = None
    beyond: bool = False              # the request also asked for more than conversion

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> Job:
        pages = d.get("pages")
        return Job(place=d.get("place"), requests=tuple(d.get("requests") or ()),
                   resolved=tuple(d.get("resolved") or ()), failed=tuple(d.get("failed") or ()),
                   wants_json=bool(d.get("wants_json")), pages=(int(pages[0]), int(pages[1])) if pages else None,
                   sheet=d.get("sheet"), beyond=bool(d.get("beyond")))


@dataclass(frozen=True)
class Pause:
    kind: str  # "source" | "match"
    job: Job
    query: str = ""
    options: tuple[dict, ...] = field(default_factory=tuple)  # Entry dicts for Q2

    def to_metadata(self) -> dict:
        return {PENDING_KEY: {"kind": self.kind, "job": self.job.to_dict(), "query": self.query,
                              "options": list(self.options)}}

    @staticmethod
    def from_metadata(metadata: dict) -> Pause | None:
        raw = metadata.get(PENDING_KEY) if isinstance(metadata, dict) else None
        if not isinstance(raw, dict) or raw.get("kind") not in ("source", "match"):
            return None
        return Pause(raw["kind"], Job.from_dict(raw.get("job") or {}), str(raw.get("query") or ""),
                     tuple(o for o in raw.get("options") or () if isinstance(o, dict)))

    def labels(self) -> list[str]:
        return unique_labels([Entry.from_dict(o) for o in self.options])

    def question(self) -> tuple[str, dict]:
        if self.kind == "source":
            options = [(sources.LABEL["drive"], "Your cloud drive"), (sources.LABEL["dropbox"], "Your file storage"),
                       (sources.PASTE_OPTION, "Paste HTML or CSV text in your next message")]
            text = "Where is the file? Pick a place, or type its link."
            return text, question_metadata(Q1_HEADER, options)
        options = [(label, "") for label in self.labels()]
        text = f"Several files match \"{self.query}\". Which one should I convert? Or type a more exact name."
        return text, question_metadata(Q2_HEADER, options)


def unique_labels(entries: list[Entry]) -> list[str]:
    seen: dict[str, int] = {}
    labels = []
    for entry in entries:
        label = entry.label()
        seen[label] = seen.get(label, 0) + 1
        labels.append(label if seen[label] == 1 else f"{label[:190]} #{seen[label]}")
    return labels


def chosen(pause: Pause, answer: str) -> Entry | None:
    """The option whose label is the answer (exact, as the card sends it), else None (free text)."""
    clean = answer.strip()
    for label, option in zip(pause.labels(), pause.options, strict=True):
        if clean == label:
            return Entry.from_dict(option)
    return None
