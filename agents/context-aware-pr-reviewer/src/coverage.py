"""Pure: the coverage lines, one per source or step that could not do its job. Worded from what code
saw (no tool offered, a tool dropped as unavailable, a step out of time), never from the model."""

from __future__ import annotations

from collections.abc import Mapping

SOURCE_OF_STEP: Mapping[str, str] = {
    "pr": "code host", "diff": "code host", "ticket": "tracker", "similar": "code host",
    "precedent": "code host", "post": "code host"}
_WHAT = {"pr": "read the pull request", "diff": "read the diff", "ticket": "read the ticket",
         "similar": "find similar merged pull requests", "precedent": "read past review comments",
         "post": "comment on the pull request"}


def no_tool(step: str) -> str:
    source = SOURCE_OF_STEP.get(step, "code host")
    return (f"No {source} tool to {_WHAT.get(step, step)} is available to this agent "
            f"(check that the {source} connector is enabled for it).")


def dropped(step: str, reasons: Mapping[str, str]) -> str:
    joined = ", ".join(dict.fromkeys(reasons.values()))
    return f"A {SOURCE_OF_STEP.get(step, 'code host')} tool to {_WHAT.get(step, step)} isn't available for your connection ({joined})."


def timed_out(step: str) -> str:
    return f"Ran out of time to {_WHAT.get(step, step)}; this uses what was fetched."


def skipped(step: str) -> str:
    return f"Skipped: {_WHAT.get(step, step)} (out of time for this turn)."
