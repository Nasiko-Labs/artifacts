"""The result of one blast-radius run, kept in the conversation's state so follow-ups need no new calls."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from affected import Affected
from plan_model import Change
from risk import LEVEL_ORDER, Risk
from search_plan import identifier

MAX_KEYS_SHOWN = 6


@dataclass(frozen=True)
class ChangeRow:
    address: str
    type: str
    action: str
    risk_level: str
    score: int
    parts: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    tag_only: bool = False
    module: str = ""
    identifier: str = ""
    keys: tuple[str, ...] = ()   # changed attribute names (values are never kept)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def build(change: Change, risk: Risk) -> ChangeRow:
        keys = tuple(k for k in change.changed_keys if change.action == "update")[:MAX_KEYS_SHOWN]
        return ChangeRow(change.address, change.rtype, change.action, risk.level, risk.score, risk.parts,
                         risk.flags, change.tag_only, change.module_path, identifier(change), keys)

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> ChangeRow:
        return ChangeRow(str(data.get("address") or ""), str(data.get("type") or ""), str(data.get("action") or ""),
                         str(data.get("risk_level") or "low"), int(data.get("score") or 0),
                         tuple(data.get("parts") or ()), tuple(data.get("flags") or ()), bool(data.get("tag_only")),
                         str(data.get("module") or ""), str(data.get("identifier") or ""),
                         tuple(data.get("keys") or ()))


@dataclass(frozen=True)
class Analysis:
    repo: str = ""
    extra_repos: tuple[str, ...] = ()
    env: str = "unknown"
    format: str = "text"
    stated: dict[str, int] | None = None
    parsed: dict[str, int] = field(default_factory=dict)
    mismatch: bool = False
    changes: tuple[ChangeRow, ...] = ()
    searched: tuple[str, ...] = ()       # addresses searched for dependents
    eligible: int = 0                    # how many could have been searched
    affected: tuple[Affected, ...] = ()
    checks: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()          # coverage lines
    code_host: bool = False              # the code host was searched
    complete: bool = False
    codeowners: bool = False
    narrative: str = ""
    breaks: tuple[tuple[str, str], ...] = ()
    view: str = ""                       # a follow-up filter note ("destroys and replacements only")

    def change(self, address: str) -> ChangeRow | None:
        return next((c for c in self.changes if c.address == address), None)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["affected"] = [a.to_dict() for a in self.affected]
        data["changes"] = [c.to_dict() for c in self.changes]
        data["breaks"] = [list(b) for b in self.breaks]
        return data

    @staticmethod
    def from_dict(data: Mapping[str, Any] | None) -> Analysis | None:
        if not data or not data.get("changes"):
            return None
        stated = data.get("stated")
        return Analysis(
            repo=str(data.get("repo") or ""), extra_repos=tuple(data.get("extra_repos") or ()),
            env=str(data.get("env") or "unknown"), format=str(data.get("format") or "text"),
            stated={k: int(v) for k, v in stated.items()} if isinstance(stated, Mapping) else None,
            parsed={k: int(v) for k, v in (data.get("parsed") or {}).items()}, mismatch=bool(data.get("mismatch")),
            changes=tuple(ChangeRow.from_dict(c) for c in data.get("changes") or ()),
            searched=tuple(data.get("searched") or ()), eligible=int(data.get("eligible") or 0),
            affected=tuple(Affected.from_dict(a) for a in data.get("affected") or ()),
            checks=tuple(data.get("checks") or ()), notes=tuple(data.get("notes") or ()),
            code_host=bool(data.get("code_host")), complete=bool(data.get("complete")),
            codeowners=bool(data.get("codeowners")), narrative=str(data.get("narrative") or ""),
            breaks=tuple((str(b[0]), str(b[1])) for b in data.get("breaks") or () if len(b) == 2))


def rank_changes(rows: list[ChangeRow]) -> tuple[ChangeRow, ...]:
    return tuple(sorted(rows, key=lambda c: (LEVEL_ORDER.get(c.risk_level, 9), -c.score, c.address)))
