"""Pure: fires → groups. The key is the alert name (case-folded), the service, the severity and the selected
labels (env, cluster); the instance / host / pod is left out, so one flapping rule across hosts is one group
while a different severity is a different group. Groups keep first-seen order."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from fires import Fire


@dataclass(frozen=True)
class Group:
    key: str
    name: str
    service: str
    severity: str
    fires: tuple[Fire, ...]

    @property
    def n(self) -> int:
        return len(self.fires)


def key_of(fire: Fire) -> str:
    labels = ",".join(f"{k}={v}" for k, v in sorted(fire.labels))
    return f"{fire.name.casefold()}|{fire.service.casefold()}|{fire.severity.casefold()}|{labels}"


def group(fires: Iterable[Fire]) -> tuple[Group, ...]:
    buckets: dict[str, list[Fire]] = {}
    for fire in fires:
        buckets.setdefault(key_of(fire), []).append(fire)
    out = []
    for key, members in buckets.items():
        ordered = tuple(sorted(members, key=lambda f: (f.t_fire, f.row)))
        first = members[0]
        out.append(Group(key, first.name, first.service, first.severity, ordered))
    return tuple(out)


def display_name(g: Group, all_groups: Iterable[Group]) -> str:
    """The alert name, with the service (and severity when needed) to tell same-named groups apart."""
    same = [o for o in all_groups if o.name.casefold() == g.name.casefold()]
    if len(same) <= 1:
        return f"{g.name} ({g.service})" if g.service else g.name
    detail = ", ".join(x for x in (g.service, g.severity) if x)
    return f"{g.name} ({detail})" if detail else g.name
