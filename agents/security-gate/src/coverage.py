"""Pure: the coverage lines, one per step that could not do its job. Worded from what code saw (no tool
offered, a tool dropped as unavailable, a step out of time), never from the model."""

from __future__ import annotations

from collections.abc import Mapping

_WHAT = {"pr": "read the pull request and its changed files", "files": "read the changed files' contents",
         "post": "comment on the pull request"}


def no_tool(step: str) -> str:
    return (f"No code host tool to {_WHAT.get(step, step)} is available to this agent "
            "(check that the code host connector is enabled for it).")


def dropped(step: str, reasons: Mapping[str, str]) -> str:
    joined = ", ".join(dict.fromkeys(reasons.values()))
    return f"A code host tool to {_WHAT.get(step, step)} isn't available for your connection ({joined})."


def timed_out(step: str) -> str:
    return f"Ran out of time to {_WHAT.get(step, step)}; this uses what was fetched."


def skipped(step: str) -> str:
    return f"Skipped: {_WHAT.get(step, step)} (out of time for this turn)."
