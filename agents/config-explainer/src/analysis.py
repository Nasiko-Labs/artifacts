"""Pure: what an analyser hands to the rest of the turn. Everything in here is computed in code."""

from __future__ import annotations

from dataclasses import dataclass, field

from gotchas import Gotcha


@dataclass(frozen=True)
class Analysis:
    kind: str                               # cron | regex | iam | nginx
    config: str
    facts: dict
    gotchas: tuple[Gotcha, ...] = ()
    checked: tuple[str, ...] = ()           # "Checked for you" lines, markdown built by code
    summary: str = ""
    explanation: str = ""                   # code-built explanation (cron); the model writes the others
    parse_error: str | None = None
    partial: bool = False                   # unresolved include, unknown flavour, …
    unsupported: bool = False               # e.g. Quartz cron: explained, no run times
    must_mention: tuple[str, ...] = ()      # statements / locations the narrative must name
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def needs_model(self) -> bool:
        return self.kind != "cron" and self.parse_error is None
