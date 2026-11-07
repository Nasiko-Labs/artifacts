"""The two questions this agent may pause on (one per pause, at most two pauses for one paste) and how an
answer is read. Reading fails closed: `Cancel` ends, an unrecognised column answer never guesses."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from inputs import current_message
from nasiko_hitl import question_metadata
from parse_csv import match_header

METADATA_KEY = "alert_noise_classifier"
NEED_SOURCE, COLUMN = "need_source", "column"
CANCEL = "Cancel"
MAX_PAUSES = 2
MAX_OPTIONS = 10
MAX_STORED_CHARS = 600_000
ROLE_WORDS = {"name": "the alert name", "fired": "the fired (triggered) time"}
_CANCEL = re.compile(r"^(?:cancel|stop|no|nope|never ?mind|abort|quit|skip)\b")


@dataclass(frozen=True)
class Pending:
    kind: str
    instruction: str = ""
    data: str = ""
    role: str = ""
    headers: tuple[str, ...] = ()
    overrides: Mapping[str, str] = field(default_factory=dict)
    pauses: int = 1

    def to_metadata(self) -> dict:
        return {METADATA_KEY: {"kind": self.kind, "instruction": self.instruction, "data": self.data,
                               "role": self.role, "headers": list(self.headers),
                               "overrides": dict(self.overrides), "pauses": self.pauses}}

    @staticmethod
    def from_metadata(metadata: Mapping[str, Any] | None) -> Pending | None:
        data = (metadata or {}).get(METADATA_KEY)
        if not isinstance(data, Mapping) or data.get("kind") not in (NEED_SOURCE, COLUMN):
            return None
        overrides = data.get("overrides") if isinstance(data.get("overrides"), Mapping) else {}
        return Pending(kind=data["kind"], instruction=str(data.get("instruction") or ""),
                       data=str(data.get("data") or ""), role=str(data.get("role") or ""),
                       headers=tuple(str(h) for h in data.get("headers") or ()),
                       overrides={str(k): str(v) for k, v in overrides.items()},
                       pauses=int(data.get("pauses") or 1))  # protobuf Struct numbers come back as floats


@dataclass(frozen=True)
class Answer:
    action: str                 # source | column | unknown | cancel
    text: str = ""
    header: str = ""


def question(pending: Pending) -> tuple[str, dict]:
    """(chat text, status-message metadata) for one pause."""
    if pending.kind == NEED_SOURCE:
        header = "Paste the fired alerts"
        text = (header + " as your answer: a CSV export with a header row, webhook JSON, or one alert per line "
                "starting with its time (at least a handful of fires). Or cancel.")
        return text, question_metadata(header, [(CANCEL, "")])
    header = "Which column is the alert name?" if pending.role == "name" else "Which column is the fired time?"
    options = [(c, "") for c in _unique(pending.headers)[:MAX_OPTIONS - 1]] + [(CANCEL, "")]
    text = f"{header} I couldn't tell which column holds {ROLE_WORDS[pending.role]}. Pick one, or type its name."
    return text, question_metadata(header, options)


def _unique(names: tuple[str, ...]) -> list[str]:
    seen, out = set(), []
    for n in names:
        name = n.strip()
        if name and name not in seen and name != CANCEL and len(name.encode()) <= 200:
            seen.add(name)
            out.append(name)
    return out


def interpret(pending: Pending, raw_answer: str) -> Answer:
    answer = current_message(raw_answer or "").strip()
    plain = " ".join(answer.casefold().split())
    if not plain or plain == CANCEL.casefold() or (_CANCEL.match(plain) and len(plain.split()) <= 3):
        return Answer("cancel")
    if pending.kind == NEED_SOURCE:
        return Answer("source", text=answer)
    header = match_header(answer, list(pending.headers))
    return Answer("column", header=header) if header is not None else Answer("unknown")


def storable(data: str) -> str:
    """The data kept on the paused task (the platform state has a size limit)."""
    return data if len(data) <= MAX_STORED_CHARS else ""
