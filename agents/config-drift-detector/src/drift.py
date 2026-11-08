"""Pure: drift records (the catalog's `drift[]`), built from comparisons, enriched with the IaC location,
the last code author and the manual actor, ranked and filtered. Frozen; every step returns new records."""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

import severity as sev
from compare import Diff
from iac import plain
from normalise import canon_key, path_text

UNKNOWN = "unknown"
_ENV_RANK = {"prod": 0, "staging": 1, "": 2, "dev": 3}
_INTERNAL = ("path", "ids", "why", "id")
_WORDS = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Drift:
    resource: str
    attribute: str
    kind: str
    expected: Any
    actual: Any
    severity: str
    file: str = ""
    line: int | None = None
    last_code_author: str = UNKNOWN
    last_code_change: str = UNKNOWN
    changed_by: Any = UNKNOWN
    environment: str = ""
    flags: tuple[str, ...] = ()
    path: tuple = ()
    ids: tuple[str, ...] = ()
    why: str = ""
    id: str = ""

    @property
    def location(self) -> str:
        return f"{self.file}:{self.line}" if self.file and self.line else self.file

    def to_dict(self) -> dict:
        """The envelope form."""
        data = {k: v for k, v in asdict(self).items() if k not in _INTERNAL}
        return {**data, "flags": list(self.flags), "why": self.why} if self.why else {**data, "flags": list(self.flags)}

    def to_state(self) -> dict:
        return {**asdict(self), "flags": list(self.flags), "path": list(self.path), "ids": list(self.ids)}

    @staticmethod
    def from_state(data: dict) -> Drift:
        return Drift(**{**data, "flags": tuple(data.get("flags") or ()), "path": tuple(data.get("path") or ()),
                        "ids": tuple(data.get("ids") or ())})


def from_diff(resource: str, diff: Diff, ids: Sequence[str], env_hints: Sequence[str],
              flags: tuple[str, ...] = ()) -> Drift:
    return Drift(
        resource=resource, attribute=path_text(diff.path) or "(resource)", kind=diff.kind,
        expected=sev.redact(diff.path, plain(diff.expected)), actual=sev.redact(diff.path, plain(diff.actual)),
        severity=sev.severity(diff.path) if diff.path else "high", environment=sev.environment(*env_hints),
        flags=flags, path=diff.path, ids=tuple(ids))


def ranked(drifts: Iterable[Drift]) -> tuple[Drift, ...]:
    ordered = sorted(drifts, key=lambda d: (sev.RANK.get(d.severity, 3), _ENV_RANK.get(d.environment, 2),
                                            d.resource, d.attribute))
    return tuple(replace(d, id=f"d{i}") for i, d in enumerate(ordered, 1))


def with_location(d: Drift, file: str, line: int | None) -> Drift:
    hints = (file, d.resource) if not d.environment else ()
    env = sev.environment(*hints) if hints else d.environment
    return replace(d, file=file, line=line, environment=env)


def resources(drifts: Iterable[Drift]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(d.resource for d in drifts))


def without_tags(drifts: Iterable[Drift]) -> tuple[Drift, ...]:
    return tuple(d for d in drifts if not sev.is_tag_path(d.path))


def matching(drifts: Iterable[Drift], target: str) -> tuple[Drift, ...]:
    """Drifts whose environment, resource type or address the target names ("prod", "security group")."""
    want = canon_key(re.sub(r"\s+", "", target or ""))
    words = set(_WORDS.findall((target or "").casefold()))
    if not want:
        return tuple(drifts)
    return tuple(d for d in drifts if d.environment in words or want in canon_key(d.resource.replace(".", ""))
                 or want in canon_key(d.attribute))


def high(drifts: Iterable[Drift]) -> tuple[Drift, ...]:
    return tuple(d for d in drifts if d.severity == "high")
