"""Pure data: the records and their (de)serialisation for task metadata and the checkpointer. What survives
the code checks is decided in split.py, proposal.py and dupcheck.py; nothing here trusts a value."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields, replace
from typing import Any

SIGNALS = ("ui", "backend", "data_migration", "external_integration", "security_or_compliance")
UNCLEAR = "unclear_scope"
STATUSES = ("proposed", "created", "skipped", "failed")


@dataclass(frozen=True)
class Spec:
    title: str
    text: str
    id: str = ""
    url: str = ""
    source: str = "document"      # document | pasted | envelope
    cut_chars: int = 0            # characters past the read limit, stated in the reply


@dataclass(frozen=True)
class Team:
    id: str
    name: str
    key: str = ""


@dataclass(frozen=True)
class Project:
    name: str
    id: str = ""                  # empty: a new project, created only after its own approval
    team_id: str = ""


@dataclass(frozen=True)
class ProposedIssue:
    local_id: int
    title: str
    description: str
    source_quote: str
    acceptance_criteria: tuple[str, ...] = ()
    signals: Mapping[str, bool] = field(default_factory=dict)
    depends_on: tuple[int, ...] = ()
    estimate: int | None = None   # computed in code (proposal.py) or set by the user; None = "?"
    user_estimate: bool = False
    exists: str = ""              # identifier of an existing issue with the same title
    exists_url: str = ""
    status: str = "proposed"
    identifier: str = ""          # once created
    url: str = ""

    @property
    def unclear(self) -> bool:
        return bool(self.signals.get(UNCLEAR)) and not self.user_estimate

    @property
    def signal_count(self) -> int:
        return sum(1 for s in SIGNALS if self.signals.get(s))


def to_dict(record: Any) -> dict:
    data = asdict(record)
    if "signals" in data:
        data["signals"] = dict(data["signals"])
    return data


def from_dict(cls: type, raw: Mapping[str, Any] | None) -> Any:
    """One record from a dict that round-tripped through task metadata (lists, floats for ints)."""
    if raw is None:
        return None
    names = {f.name for f in fields(cls)}
    values = {k: _thaw(k, v) for k, v in raw.items() if k in names}
    return cls(**values)


def _thaw(key: str, value: Any) -> Any:
    if key == "depends_on":
        return tuple(int(v) for v in value or ())
    if key in ("local_id",):
        return int(value)
    if key == "estimate":
        return None if value is None else int(value)
    if key == "cut_chars":
        return int(value or 0)
    if key == "signals":
        return {str(k): bool(v) for k, v in dict(value or {}).items()}
    if isinstance(value, list):
        return tuple(value)
    return value


def issues_from(raw: Any) -> tuple[ProposedIssue, ...]:
    return tuple(from_dict(ProposedIssue, item) for item in raw or ())


def issues_to(issues: Any) -> list[dict]:
    return [to_dict(i) for i in issues]


def with_status(issue: ProposedIssue, status: str, **changes: Any) -> ProposedIssue:
    return replace(issue, status=status, **changes)
