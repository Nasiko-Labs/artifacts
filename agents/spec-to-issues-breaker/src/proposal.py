"""Pure method, in code: estimates from the issue's signals, dependency validation and cycle removal,
topological creation order, and the size hint. The model never sizes, counts or orders anything."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from records import ProposedIssue

POINTS = (1, 2, 3, 5, 8)
MAX_DESCRIPTION_WORDS = 120
SPLIT_SIGNALS = 4


def estimate_of(issue: ProposedIssue) -> int | None:
    """[1,2,3,5,8][min(n,4)] for n true signals; None ("?") when the scope is unclear, unless the user set one."""
    if issue.user_estimate:
        return issue.estimate
    if issue.unclear:
        return None
    return POINTS[min(issue.signal_count, len(POINTS) - 1)]


def with_estimates(issues: Sequence[ProposedIssue]) -> tuple[ProposedIssue, ...]:
    return tuple(replace(i, estimate=estimate_of(i)) for i in issues)


def needs_split(issue: ProposedIssue) -> bool:
    return len(issue.description.split()) > MAX_DESCRIPTION_WORDS or issue.signal_count >= SPLIT_SIGNALS


def clean_dependencies(issues: Sequence[ProposedIssue]) -> tuple[tuple[ProposedIssue, ...], tuple[str, ...]]:
    """Unknown and self ids dropped; each edge that closes a cycle (DFS order) removed and noted."""
    ids = {i.local_id for i in issues}
    current = {i.local_id: tuple(d for d in dict.fromkeys(i.depends_on) if d in ids and d != i.local_id)
               for i in issues}
    notes: list[str] = []
    state: dict[int, int] = {}  # 1 = on the DFS stack, 2 = done

    def visit(node: int) -> None:
        state[node] = 1
        for dep in current[node]:
            if state.get(dep) == 1:
                current[node] = tuple(d for d in current[node] if d != dep)
                notes.append(f"Removed the dependency #{node} -> #{dep}: it closed a cycle.")
            elif dep not in state:
                visit(dep)
        state[node] = 2

    for issue in issues:
        if issue.local_id not in state:
            visit(issue.local_id)
    return tuple(replace(i, depends_on=current[i.local_id]) for i in issues), tuple(notes)


def creation_order(issues: Sequence[ProposedIssue]) -> tuple[ProposedIssue, ...]:
    """Topological order (dependencies first), ties kept in list order. Assumes cycles were removed."""
    by_id = {i.local_id: i for i in issues}
    done: list[int] = []

    def place(node: int, path: frozenset[int]) -> None:
        if node in done or node in path:
            return
        for dep in by_id[node].depends_on:
            if dep in by_id:
                place(dep, path | {node})
        done.append(node)

    for issue in issues:
        place(issue.local_id, frozenset())
    return tuple(by_id[n] for n in done)


def renumber(issues: Sequence[ProposedIssue]) -> tuple[ProposedIssue, ...]:
    """Local ids 1..n in list order, dependencies remapped; ids the list no longer holds are dropped."""
    mapping = {issue.local_id: n for n, issue in enumerate(issues, start=1)}
    return tuple(replace(i, local_id=mapping[i.local_id],
                         depends_on=tuple(mapping[d] for d in i.depends_on if d in mapping)) for i in issues)
