"""The follow-up `draft`: fetch the chosen test's file (by its path, or by a code search for the unique
definition), look for an open ticket that already names the test, pick the write tools in code, and build
the plan. No write happens here; the plan goes to the four-card walk."""

from __future__ import annotations

import logging
from collections.abc import Sequence

import patch as patcher
import prompts
import records
import send_guard
from deps import TurnDeps
from discovery import Discoverer
from history import Flaky
from nasiko_mcp import McpTool
from reader import TurnReader
from writes import Plan, branch_name

log = logging.getLogger("flaky_hunter.draft")

FILE_CALLS, FILE_SECS = 3, 90.0
TICKET_CALLS, TICKET_SECS = 2, 60.0


async def prepare(deps: TurnDeps, report: dict, item: Flaky) -> Plan | str:
    """The plan for the walk, or the reply that ends the turn."""
    repo = str(report.get("repo") or "")
    if "/" not in repo:
        return "These results came from pasted logs, so I don't know the repository. Name it (`owner/repo`) first."
    discoverer = Discoverer(deps)
    failure = await discoverer.start()
    if failure:
        return failure
    reader = TurnReader(deps, discoverer)
    base = str(report.get("base") or "")
    if not base:
        return "I couldn't tell the repository's default branch from the CI data, so I can't draft a pull request."
    path = item.file if patcher.language(item.file) else ""
    await reader.run("file", prompts.file_system(repo, base, path, _needle(item), deps.now), "Fetch it.",
                     FILE_CALLS, FILE_SECS)
    texts = [c.text for c in reader.ledger.of("file")]
    owner, name = repo.split("/", 1)
    plan = Plan(owner, name, base, next((h for t in texts if (h := records.branch_head(t, base))), "")
                or str(report.get("base_sha") or ""), item.to_dict(), scope=_scope(item.test),
                branch=branch_name(item.name))
    plan = _with_file(plan, item, texts)
    existing = await _existing_issue(reader, repo, item.test, deps)
    tickets = (await discoverer.catalog("ticket")).write_tools()
    prs = (await discoverer.catalog("pr")).write_tools()
    tools = {k: t.name for k, t in (("issue", _pick(tickets, "issue")), ("comment", _pick(tickets, "comment")),
                                    ("branch", _pick(prs, "branch")), ("commit", _pick(prs, "commit")),
                                    ("pr", _pick(prs, "pr"))) if t is not None}
    if existing and "comment" not in tools:
        tools = {k: v for k, v in tools.items() if k != "issue"}
    return Plan(**{**plan.to_dict(), "existing_issue": existing, "tools": tools})


def _needle(item: Flaky) -> str:
    return {"python": f"def {item.name}", "go": f"func {item.name}", "rust": f"fn {item.name}"}.get(
        patcher.language(item.file), item.name)


def _scope(test_id: str) -> str:
    parts = test_id.split("::")
    return parts[1] if len(parts) == 3 and parts[0].endswith(".py") else ""


def _with_file(plan: Plan, item: Flaky, texts: Sequence[str]) -> Plan:
    found = [f for t in texts for f in records.files(t)]
    hits = list(dict.fromkeys(p for t in texts for p in records.search_paths(t) if patcher.language(p)))
    if item.file:
        wanted = [f for f in found if f.path == item.file]
    elif len(hits) > 1:
        return Plan(**{**plan.to_dict(), "patch_error": f"`{item.name}` matches {len(hits)} files, so I can't tell "
                                                         "which one to patch; I'll offer the ticket only."})
    else:
        wanted = [f for f in found if not hits or f.path in hits]
    if not wanted:
        return Plan(**{**plan.to_dict(), "path": item.file,
                       "patch_error": "I couldn't fetch the test file, so I'll offer the ticket only."})
    file = wanted[0]
    return Plan(**{**plan.to_dict(), "path": file.path, "file_sha": file.sha, "original": file.text})


async def _existing_issue(reader: TurnReader, repo: str, test_id: str, deps: TurnDeps) -> str:
    await reader.run("ticket", prompts.ticket_search_system(repo, test_id, deps.now), "Search.",
                     TICKET_CALLS, TICKET_SECS)
    found = [i for c in reader.ledger.of("ticket") for i in records.issues(c.text)]
    return next((i.number for i in found if i.state == "open" and (test_id in i.title or test_id in i.body)), "")


def _pick(tools: Sequence[McpTool], want: str) -> McpTool | None:
    """The write tool for each card, by the words in its name."""
    def fits(tool: McpTool) -> bool:
        t = set(send_guard.name_tokens(tool.name))
        creates = bool(t & {"create", "open", "add", "new", "post", "put"})
        if t & {"delete", "remove", "merge", "close", "review", "reaction"}:
            return False
        if want == "issue":
            return "issue" in t and "comment" not in t and creates
        if want == "comment":
            return "comment" in t and not t & {"commit", "update", "edit", "pull"} and creates
        if want == "branch":
            return bool(t & {"branch", "ref", "reference"}) and creates and not t & {"pull", "file", "update"}
        if want == "commit":
            return bool(t & {"file", "files", "contents", "content"}) and bool(t & {"create", "update", "put"})
        return "pull" in t and creates and not t & {"comment", "update"}
    return next((t for t in tools if fits(t)), None)
