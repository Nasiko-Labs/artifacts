"""The code-host step: fetch the IaC files (by path, or found by searching for each resource block) and the
commits that changed them. The model drives the read tools in one bounded loop; code reads the files
and commits out of the raw tool output afterwards. Whatever could not be read becomes a coverage line."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import discovery
import prompts
import records
from analyse import Sources
from deps import FETCH_SECS, TurnDeps
from fence import Ledger, RecordingGateway
from readonly import ReadOnlyGateway
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("drift.fetch")

LOOP_CALLS = 4
_PAUSED = ("approval_pending", "authorization_required")
_NOTES = {
    discovery.NO_GATEWAY: "The code host isn't reachable from this agent (no gateway), so IaC files, lines and code "
                          "authors are unknown.",
    discovery.UNREACHABLE: "The code host couldn't be reached just now, so IaC files, lines and code authors are "
                           "unknown.",
    discovery.NO_TOOLS: "No code host read tool is available to this agent: check that the code-host connector is "
                        "enabled for it. IaC files, lines and code authors are unknown.",
}
NO_REPO_NOTE = ("No repository named, so IaC files, lines and code authors are unknown. Name it (`owner/repo`) to "
                "add them.")


@dataclass(frozen=True)
class Fetched:
    sources: Sources
    notes: tuple[str, ...] = ()
    gaps: bool = False


EMPTY = Sources({}, {}, "none")


def norm(path: str) -> str:
    return path.strip().removeprefix("./").strip("/")


async def fetch(deps: TurnDeps, repo: str, ref: str, paths: Sequence[str], blocks: Sequence[str]) -> Fetched:
    if not repo:
        return Fetched(EMPTY, (NO_REPO_NOTE,), True)
    catalog, reason = await discovery.discover(deps)
    if catalog is None:
        return Fetched(EMPTY, (_NOTES[reason],), True)
    ledger = Ledger()
    health = ToolHealth(RecordingGateway(ReadOnlyGateway(deps.gateway, catalog.writes), ledger))
    messages = ({"role": "system", "content": prompts.FETCH_SYSTEM},
                {"role": "user", "content": prompts.fetch_task(repo, ref, tuple(paths), tuple(blocks))})
    budget = min(FETCH_SECS, deps.remaining())
    notes: list[str] = []
    try:
        result = await asyncio.wait_for(
            run_tool_loop(deps.llm, deps.model, messages, catalog.tools, health, deps.traceparent,
                          max_iterations=LOOP_CALLS, is_offered=health.is_offered), timeout=max(budget, 1.0))
        if result.status in _PAUSED:
            notes.append(prompts.READ_NEEDS_APPROVAL)
    except TimeoutError:
        log.warning("code host fetch hit its %.0fs limit", budget)
        notes.append("The code host fetch ran out of time; this uses what it had fetched.")
    sources = _sources(ledger)
    if health.dropped:
        reasons = ", ".join(dict.fromkeys(health.dropped.values()))
        notes.append(f"A code host capability isn't available for your connection ({reasons}).")
    missing = [p for p in paths if norm(p) not in sources.files]
    notes += [f"Couldn't fetch {p} from {repo}." for p in missing]
    if not sources.files and not missing:
        notes.append(f"No IaC file for these resources was found in {repo} on the code host, so files, lines and "
                     "code authors are unknown.")
    log.info("fetched %d files, commits for %d paths", len(sources.files), len(sources.commits))
    return Fetched(sources, tuple(notes), bool(notes))


def _sources(ledger: Ledger) -> Sources:
    files: dict[str, str] = {}
    commits: dict[str, tuple[records.Commit, ...]] = {}
    for call in ledger.calls:
        for found in records.files(call.text, call.arguments):
            files.setdefault(norm(found.path), found.text)
        listed = records.commits(call.text)
        asked = norm(records.path_argument(call.arguments))
        if listed and asked:
            commits[asked] = (*commits.get(asked, ()), *listed)
    return Sources(files, commits, "code host" if files else "none")
