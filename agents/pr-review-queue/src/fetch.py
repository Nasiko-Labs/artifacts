"""Read the code host for one turn: list each repository's open pull requests (concurrently), then the
per-PR reads (detail, reviews, comments, commits, checks) as code-driven calls, at most
`MAX_IN_FLIGHT` at a time, inside the call budget and the deadlines. Whatever is not read becomes a
coverage line; nothing is guessed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace

import callers
import list_step
import prompts
import records
from deps import TurnDeps
from discovery import Discoverer
from facts import PRFacts, last_activity_at
from nasiko_mcp import (
    ApprovalPending,
    AuthorizationRequired,
    McpGatewayError,
    McpTool,
    ToolOk,
)
from tool_health import ToolHealth
from view import Snapshot

log = logging.getLogger("pr_review_queue.fetch")

MAX_IN_FLIGHT = 6
TOOL_CALL_BUDGET = 300
PER_REPO_CAP, OVERALL_CAP = 60, 100
LIST_SECS, FANOUT_SECS = 60.0, 150.0
READ_STEPS = ("list", "detail", "reviews", "checks")
DETAIL_READS = ("get_pr", "reviews", "comments", "review_comments", "commits")
LABEL = {"get_pr": "merge state", "reviews": "reviews", "comments": "comments", "review_comments": "review comments",
         "commits": "commits", "check_runs": "checks", "status": "checks"}


@dataclass
class Budget:
    left: int = TOOL_CALL_BUDGET
    failures: dict[str, str] = field(default_factory=dict)   # read label → why it could not be read
    paused: bool = False


class Fetcher:
    def __init__(self, deps: TurnDeps):
        self._deps = deps
        self._health = ToolHealth(deps.gateway)
        self._slots = asyncio.Semaphore(MAX_IN_FLIGHT)
        self._budget = Budget()
        self._tools: tuple[McpTool, ...] = ()

    async def read(self, repos: Sequence[str]) -> Snapshot | str:
        """The snapshot, or the fixed reply that ends the turn (no code host, no trace context ...)."""
        discoverer = Discoverer(self._deps)
        failure = await discoverer.start()
        if failure:
            return failure
        catalogs = await discoverer.catalogs(READ_STEPS)
        self._tools = callers.unique([t for c in catalogs.values() for t in c.read_tools()])
        if not any(callers.fits(t, "list_prs") for t in self._tools):
            return prompts.NO_CODE_HOST
        listed = await asyncio.gather(*(self._list(r) for r in repos))
        notes = [n for _, n in listed if n]
        failed = tuple(r for r, (prs, n) in zip(repos, listed, strict=True) if prs is None)
        found = [f for prs, _ in listed for f in prs or ()]
        if self._budget.paused:
            return prompts.READ_NEEDS_APPROVAL
        if failed and len(failed) == len(repos):
            return "\n\n".join([prompts.NOTHING_READ, *notes])
        kept, cut = _cap(found)
        inspected = await self._inspect_all(kept)
        notes += self._coverage(inspected, cut)
        partial = any(not f.inspected for f in inspected) or bool(self._budget.failures)
        return Snapshot(tuple(inspected), tuple(notes), failed, cut, partial, False, prompts.iso(self._deps.now))

    async def _list(self, repo: str) -> tuple[list[PRFacts] | None, str]:
        owner, name = repo.split("/", 1)
        context = {"owner": owner, "repo": name, "list": True}
        chosen = callers.pick(self._tools, "list_prs", context)
        try:
            if chosen is None:
                texts = await asyncio.wait_for(list_step.run(self._deps, self._health, self._tools, repo), LIST_SECS)
            else:
                text = await asyncio.wait_for(self._call(*chosen), LIST_SECS)
                texts = [text] if text is not None else []
        except TimeoutError:
            return None, f"{repo}: listing its pull requests ran out of time; not read."
        prs = [p for t in texts for p in records.pull_requests(t)]
        if not texts:
            why = self._budget.failures.get("list", "the listing failed")
            return None, f"{repo}: could not list its pull requests ({why}); not read."
        return [PRFacts(repo, p) for p in prs], ""

    async def _call(self, tool: McpTool, args: dict, label: str = "list") -> str | None:
        """One read through tool health; the raw text, or None (the reason kept for the coverage)."""
        if not self._health.is_offered(tool.name):
            self._budget.failures.setdefault(label, self._health.dropped[tool.name])
            return None
        if self._budget.left <= 0:
            self._budget.failures.setdefault(label, "call budget used up")
            return None
        self._budget.left -= 1
        async with self._slots:
            try:
                result = await self._health.call_tool(tool.name, args, self._deps.traceparent)
            except McpGatewayError as e:
                log.warning("%s failed: %s", tool.name, type(e).__name__)
                self._budget.failures.setdefault(label, "the tool failed")
                return None
        if isinstance(result, (ApprovalPending, AuthorizationRequired)):
            self._budget.paused = True
            return None
        if not isinstance(result, ToolOk) or result.is_error:
            reason = self._health.dropped.get(tool.name) or "the tool returned an error"
            self._budget.failures.setdefault(label, reason)
            return None
        return result.text

    async def _inspect_all(self, prs: list[PRFacts]) -> list[PRFacts]:
        tasks = [asyncio.ensure_future(self._inspect(f)) for f in prs]
        done, pending = await asyncio.wait(tasks, timeout=FANOUT_SECS) if tasks else (set(), set())
        for task in pending:
            task.cancel()
        return [t.result() if t in done and not t.exception() else f for t, f in zip(tasks, prs, strict=True)]

    async def _inspect(self, f: PRFacts) -> PRFacts:
        owner, name = f.repo.split("/", 1)
        context = {"owner": owner, "repo": name, "number": f.pr.number}
        chosen = {p: callers.pick(self._tools, p, context) for p in DETAIL_READS}
        texts = await asyncio.gather(*(self._call(*c, LABEL[p]) if c else _none() for p, c in chosen.items()))
        got = dict(zip(DETAIL_READS, texts, strict=True))
        detail = next(iter(records.pull_requests(got["get_pr"] or "")), None)
        pr = _merge_pr(f.pr, detail)
        checks = await self._checks(owner, name, pr.head_sha)
        reviews = tuple(records.reviews(got["reviews"] or ""))
        comments = tuple(records.comments(got["comments"] or "")) + tuple(records.comments(got["review_comments"] or ""))
        commits = tuple(records.commit_dates(got["commits"] or ""))
        read_any = got["reviews"] is not None or got["commits"] is not None
        return f.with_detail(pr=pr, reviews=reviews, comments=comments, commit_dates=commits, checks=checks,
                             inspected=read_any, reviews_read=got["reviews"] is not None)

    async def _checks(self, owner: str, name: str, sha: str) -> str:
        if not sha:
            return ""
        context = {"owner": owner, "repo": name, "sha": sha}
        chosen = [c for c in (callers.pick(self._tools, p, context) for p in ("check_runs", "status")) if c]
        if not chosen:
            self._budget.failures.setdefault("checks", "no tool for checks")
            return ""
        texts = [t for t in await asyncio.gather(*(self._call(*c, "checks") for c in chosen)) if t is not None]
        if not texts:
            return ""
        states = [records.check_state(t) for t in texts]
        return records.summarize_checks([x for x in states if x]) or "none"

    def _coverage(self, prs: list[PRFacts], cut: int) -> list[str]:
        notes = []
        if cut:
            notes.append(f"{cut} more open pull requests were not inspected (cap {OVERALL_CAP} overall, "
                         f"{PER_REPO_CAP} per repository; the oldest activity first).")
        unread = [f for f in prs if not f.inspected]
        if unread:
            notes.append(f"{len(unread)} pull requests not inspected (time or call limit); their quiet days come "
                         "from the listing's update time.")
        for label, why in self._budget.failures.items():
            if label == "list":
                continue
            limited = " blocked_by is limited to checks and conflicts." if label == "reviews" else ""
            notes.append(f"Code host {label} not read ({why}).{limited}")
        return notes


async def _none() -> None:
    return None


def _merge_pr(listed: records.PullRequest, detail: records.PullRequest | None) -> records.PullRequest:
    if detail is None:
        return listed
    return replace(listed, head_sha=detail.head_sha or listed.head_sha,
                   requested=detail.requested or listed.requested, teams=detail.teams or listed.teams,
                   conflict=detail.conflict if detail.conflict is not None else listed.conflict,
                   changed_lines=detail.changed_lines or listed.changed_lines, url=listed.url or detail.url)


def _cap(found: list[PRFacts]) -> tuple[list[PRFacts], int]:
    """Oldest activity first: at most PER_REPO_CAP per repository and OVERALL_CAP in all."""
    def oldest(f: PRFacts):
        moment = last_activity_at(f)
        return (moment.timestamp() if moment else 0.0, int(f.pr.number))
    per_repo: dict[str, list[PRFacts]] = {}
    for f in sorted(found, key=oldest):
        per_repo.setdefault(f.repo, []).append(f)
    capped = sorted((f for fs in per_repo.values() for f in fs[:PER_REPO_CAP]), key=oldest)
    kept = capped[:OVERALL_CAP]
    return kept, len(found) - len(kept)
