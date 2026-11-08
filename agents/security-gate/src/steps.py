"""The two read steps. `pr`: the model drives the code-host tools to get the pull request and its changed
files; code reads the facts out of the turn's ledger. `files`: the model fetches the FIRST file's contents
at the head commit, then code replays that same call with only the path substituted for every other file
(no further model calls). Paths only ever come from the pull request's own file list."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import prompts
import tool_data
from fence import Call
from reader import TurnReader
from records import ChangedFile, PullRequest

log = logging.getLogger("security_gate.steps")

PR_CALLS, PR_SECS = 3, 60.0
FILES_CALLS, FILES_SECS = 5, 60.0
MAX_FILE_BYTES = 200_000
MAX_TOTAL_BYTES = 1_000_000
REPLAY_CONCURRENCY = 5


@dataclass(frozen=True)
class FoundPr:
    pr: PullRequest | None
    files: tuple[ChangedFile, ...] = ()
    reply: str = ""
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Contents:
    by_path: Mapping[str, str] = field(default_factory=dict)
    skipped: tuple[tuple[str, str], ...] = ()
    notes: tuple[str, ...] = ()


async def find_pr(reader: TurnReader, repo: str, number: str) -> FoundPr:
    step = await reader.run("pr", prompts.pr_system(repo, number), "Fetch it.", PR_CALLS, PR_SECS)
    if step.status == "paused":
        return FoundPr(None, reply=prompts.READ_NEEDS_APPROVAL)
    if step.status == "no_tools":
        return FoundPr(None, reply=f"{step.note} {prompts.NO_TOOLS_PASTE}")
    texts = [c.text for c in reader.ledger.of("pr")]
    pr = next((p for t in texts if (p := tool_data.pull_request(t, repo, number)) is not None), None)
    files = _merge_files(f for t in texts for f in tool_data.changed_files(t))
    if pr is None and not files:
        hint = (" None of the tools available to me returned it: check that the code host connector is enabled "
                "for this agent, or paste the diff (`diff --git` text) instead.")
        extra = f" {step.note}" if step.note else ""
        return FoundPr(None, reply=f"I couldn't read pull request {repo}#{number}.{hint}{extra}")
    notes = (step.note,) if step.note else ()
    return FoundPr(pr or PullRequest(repo, number), files, notes=notes)


def _merge_files(found) -> tuple[ChangedFile, ...]:
    by_path: dict[str, ChangedFile] = {}
    for f in found:
        if f.patch or f.path not in by_path:
            by_path[f.path] = f
    return tuple(by_path.values())


async def fetch_contents(reader: TurnReader, pr: PullRequest, wanted: Sequence[str]) -> Contents:
    """Head-commit contents of `wanted` (≤ 30 paths, from the PR's file list), within the size caps."""
    if not wanted:
        return Contents()
    run = await reader.run("files", prompts.files_system(pr.repo, wanted[0], pr.head_sha), "Fetch it.",
                           FILES_CALLS, FILES_SECS)
    notes = (run.note,) if run.note else ()
    got = _from_calls(reader.ledger.of("files"), wanted)
    template = _template(reader.ledger.of("files"), wanted)
    rest = [p for p in wanted if p not in got]
    if template is not None and rest:
        got = {**got, **await _replay(reader, template, rest)}
    elif rest and template is None:
        log.info("no file-contents call succeeded; %d files fall back to their hunks", len(rest))
    return _capped(got, wanted, notes)


def _path_arg(arguments: Mapping[str, Any], wanted: Sequence[str]) -> tuple[str, str] | None:
    for key, value in arguments.items():
        if isinstance(value, str) and value in wanted:
            return key, value
    return None


def _from_calls(calls: Sequence[Call], wanted: Sequence[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for call in calls:
        hit = _path_arg(call.arguments, wanted)
        if hit is None:
            continue
        content = tool_data.file_content(call.text, hit[1])
        if content is not None:
            out.setdefault(hit[1], content)
    return out


def _template(calls: Sequence[Call], wanted: Sequence[str]) -> tuple[str, dict, str] | None:
    """(tool, arguments, path key) of the first call that returned a file's contents."""
    for call in calls:
        hit = _path_arg(call.arguments, wanted)
        if hit is not None and tool_data.file_content(call.text, hit[1]) is not None:
            return call.tool, dict(call.arguments), hit[0]
    return None


async def _replay(reader: TurnReader, template: tuple[str, dict, str], paths: Sequence[str]) -> dict[str, str]:
    tool, arguments, key = template
    gate = asyncio.Semaphore(REPLAY_CONCURRENCY)

    async def one(path: str) -> tuple[str, str | None]:
        async with gate:
            text = await reader.replay("files", tool, {**arguments, key: path})
        return path, (tool_data.file_content(text, path) if text is not None else None)

    results = await asyncio.gather(*(one(p) for p in paths))
    return {p: c for p, c in results if c is not None}


def _capped(got: Mapping[str, str], wanted: Sequence[str], notes: tuple[str, ...]) -> Contents:
    kept: dict[str, str] = {}
    skipped: list[tuple[str, str]] = []
    total = 0
    for path in wanted:
        content = got.get(path)
        if content is None:
            continue
        size = len(content.encode("utf-8"))
        if size > MAX_FILE_BYTES:
            skipped.append((path, "over 200 KB"))
            continue
        if total + size > MAX_TOTAL_BYTES:
            skipped.append((path, "over the 1 MB total"))
            continue
        total += size
        kept[path] = content
    return Contents(kept, tuple(skipped), notes)
