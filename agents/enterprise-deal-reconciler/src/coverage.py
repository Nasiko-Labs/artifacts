"""Pure: whether a source (documents, payments) could actually be read this turn, and what to tell the user
when it could not. Decided from the offered tools and the tools dropped as unavailable, never from the
model's prose. (Adapted from the hardened kit; owned by this agent.)"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from nasiko_mcp import McpTool

State = Literal["ok", "missing", "unavailable"]
_LABEL = {"payments": "Payments", "documents": "Document store"}
_NOUN = {"payments": "payments", "documents": "document store"}


@dataclass(frozen=True)
class SourceStatus:
    source: str
    state: State
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"


def assess(source: str, offered: Sequence[McpTool], dropped: Mapping[str, str],
           helpers: frozenset[str] = frozenset()) -> SourceStatus:
    """`missing` when no tool was offered for the source, `unavailable` when every one was dropped."""
    tools = [t for t in offered if t.name not in helpers]
    if not tools:
        return SourceStatus(source, "missing")
    reasons = [dropped[t.name] for t in tools if t.name in dropped]
    if len(reasons) == len(tools):
        return SourceStatus(source, "unavailable", ", ".join(dict.fromkeys(reasons)))
    return SourceStatus(source, "ok")


def line(status: SourceStatus) -> str:
    label, noun = _LABEL.get(status.source, status.source), _NOUN.get(status.source, status.source)
    if status.state == "unavailable":
        return f"{label} access isn't available for your connection ({status.reason})."
    return f"no {noun} tool is available to this agent: check that the {noun} connector is enabled for it."


def payments_failed_reply(status: SourceStatus) -> str:
    return ("I couldn't read the billing configuration, so I can't reconcile, and I won't guess it.\n\n"
            f"- {line(status)}\n\nYou can paste the billing setup after `Billing:` as `key: value` lines "
            "(quantity, unit_amount, currency, interval, anchor_date, end_date, percent_off, days_until_due).")


def documents_note(status: SourceStatus) -> str:
    return f"Contract not read from your documents: {line(status)}"
