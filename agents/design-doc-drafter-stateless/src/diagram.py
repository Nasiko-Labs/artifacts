"""Pure: one ASCII diagram generated from the components' `depends_on`. Boxes and arrows, one line per
dependency; a component with no edges gets its own box line. More than 12 components: no diagram (the
components table already lists them)."""

from __future__ import annotations

from collections.abc import Sequence

MAX_COMPONENTS = 12


def diagram(components: Sequence[dict]) -> str | None:
    if not components or len(components) > MAX_COMPONENTS:
        return None
    width = max(len(c["name"]) for c in components) + 2
    lines: list[str] = []
    linked: set[str] = set()
    for comp in components:
        for dep in comp["depends_on"]:
            lines.append(f"{_box(comp['name'], width)} ---> {_box(dep, 0)}")
            linked.update((comp["name"], dep))
    lines += [_box(c["name"], width).rstrip() for c in components if c["name"] not in linked]
    return "```text\n" + "\n".join(line.rstrip() for line in lines) + "\n```"


def _box(name: str, width: int) -> str:
    return f"[{name}]".ljust(width)
