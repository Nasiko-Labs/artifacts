"""Pure: one declaration's exposure to the advisory (the status table), and the exposure ranking.

    affected              a resolved (lockfile) or pinned version is inside the affected range
    may_resolve_affected  the declared range overlaps the affected range and no lockfile says what resolved
    not_affected          resolved/pinned outside, or the declared range cannot reach the affected range
    unknown               the declared version or the affected range cannot be read
    deprecated            a deprecation notice: declared at all is the exposure

Rank (code): affected runtime 4, affected dev 3, may_resolve_affected 2, unknown 1, not_affected 0;
ties by repository, then manifest path. `not_affected` rows are listed only when asked.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass

import bump
import ranges
import versions
from advisory import Advisory
from dependency import Dep

LISTED = ("affected", "may_resolve_affected", "unknown", "deprecated")
EXPOSED = ("affected", "may_resolve_affected", "deprecated")


@dataclass(frozen=True)
class Exposure:
    repo: str
    manifest: str
    package: str
    ecosystem: str
    declared: str
    resolved: str | None
    scope: str
    status: str
    fixed: str | None
    bump_to: str | None
    url: str | None = None
    major_bump: bool = False
    line: int = 0
    editable: bool = False
    alert: bool = False   # the code host's own dependency alert names this package (corroboration only)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: Mapping) -> Exposure:
        fields = Exposure.__dataclass_fields__
        return Exposure(**{k: v for k, v in dict(data).items() if k in fields})


def assess(dep: Dep, resolved: Sequence[str], advisory: Advisory, repo: str, url: str | None = None,
           alert: bool = False) -> Exposure:
    status, current = _status(dep, resolved, advisory)
    fixed = ", ".join(advisory.fixed) or None
    target, major = (None, False)
    if status in ("affected", "may_resolve_affected") and advisory.fixed:
        target, major = bump.target(dep.ecosystem, advisory.fixed, current)
    return Exposure(repo=repo, manifest=dep.path, package=dep.name, ecosystem=dep.ecosystem, declared=dep.spec,
                    resolved=", ".join(resolved) or None, scope=dep.scope, status=status, fixed=fixed,
                    bump_to=target, url=url, major_bump=major, line=dep.line, editable=dep.editable, alert=alert)


def _status(dep: Dep, resolved: Sequence[str], advisory: Advisory) -> tuple[str, str | None]:
    """(status, the version the bump starts from)."""
    if advisory.kind == "deprecation":
        return "deprecated", None
    affected = advisory.intervals()
    declared = ranges.parse_declared(dep.ecosystem, dep.spec)
    floor = declared.minimum.text if declared and declared.minimum else None
    if affected is None:
        return "unknown", floor
    locked = [v for v in (versions.parse(dep.ecosystem, r) for r in resolved) if v is not None]
    if locked:
        hit = any(ranges.contains(affected, v) for v in locked)
        return ("affected" if hit else "not_affected"), floor or min(locked).text
    if declared is None:
        return "unknown", None
    if declared.exact and declared.minimum is not None:
        return ("affected" if ranges.contains(affected, declared.minimum) else "not_affected"), floor
    return ("may_resolve_affected" if ranges.overlaps(declared.intervals, affected) else "not_affected"), floor


def score(row: Exposure) -> int:
    if row.status in ("affected", "deprecated"):
        return 4 if row.scope == "runtime" else 3
    return {"may_resolve_affected": 2, "unknown": 1}.get(row.status, 0)


def ranked(rows: Iterable[Exposure], include_not_affected: bool = False) -> list[Exposure]:
    kept = [r for r in rows if include_not_affected or r.status in LISTED]
    return sorted(kept, key=lambda r: (-score(r), r.repo.casefold(), r.manifest, r.package))
