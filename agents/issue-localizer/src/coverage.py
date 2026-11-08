"""Pure: the coverage lines the reply ends with: what was read, what could not be, and why. Decided from the
counts and failures the code host reader kept, never from the model."""

from __future__ import annotations

from collections.abc import Sequence

from codehost import Coverage

_STEP_LABEL = {"issue": "issue read", "comments": "issue comments", "repo": "repository details",
               "list": "directory listing", "search": "code search", "file": "file reads"}


def lines(cov: Coverage | None, pasted_only: bool, has_search: bool, dropped: int,
          extra: Sequence[str] = ()) -> list[str]:
    out: list[str] = []
    if pasted_only:
        out.append("no repository access: ranked only among the files you pasted")
    elif cov is not None:
        out.append(f"listed {cov.dirs_listed} director{'y' if cov.dirs_listed == 1 else 'ies'}, "
                   f"{cov.searches} code search{'es' if cov.searches != 1 else ''}, read {cov.files_read} "
                   f"file{'s' if cov.files_read != 1 else ''}" + (f" ({cov.truncated} truncated at 12k chars)"
                                                                 if cov.truncated else ""))
        if not has_search or "search" in cov.failed:
            why = cov.failed.get("search", "no code search tool is available")
            out.append(f"code search unavailable ({why}): ranked by path names only")
        out += [f"{_STEP_LABEL[s]} unavailable ({why})" for s, why in cov.failed.items() if s != "search"]
        if cov.budget_hit:
            out.append("stopped at the tool-call or time budget, so some reads were skipped")
    if dropped:
        out.append(f"{dropped} model answer item{'s' if dropped != 1 else ''} dropped because they did not match "
                   "the repository")
    return [*out, *extra]
