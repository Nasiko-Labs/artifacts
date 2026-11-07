"""The tracker side: one bounded read loop (teams, projects, labels, issues matching each thread), its
answer checked in code against the tool output; the duplicate test (thread link in an issue description, or
a title close to the proposed title or the action's first words); and the one guarded create call."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

import create_guard
import issue
import prompts
from create_guard import CreateApproval
from deps import TurnDeps
from fence import FencedGateway
from llmcall import load_json_object, text_of
from models import Finding, Named, TrackerContext
from nasiko_mcp import McpTool
from sender import WriteResult, resume_write, write_once
from textcheck import appears_in
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("thread_to_issue.tracker")

TRACKER_LOOP_CALLS = 6
ISSUE_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
_URL = re.compile(r"https?://[^\s\"'<>)\]]+")
_MIN_REF_CHARS = 8  # a shorter id would match by accident


@dataclass(frozen=True)
class Existing:
    key: str
    url: str
    title: str
    description: str


@dataclass(frozen=True)
class TrackerRead:
    context: TrackerContext
    issues: tuple[Existing, ...] = ()


async def read(deps: TurnDeps, tools: Sequence[McpTool], family: str, findings: Sequence[Finding]) -> TrackerRead:
    """Teams, projects and labels plus existing issues for the findings; `dup_check_ok` is False when the
    read did not finish, so confidence drops and nothing is called a duplicate on a guess."""
    if not tools:
        return TrackerRead(TrackerContext(family=family, dup_check_ok=False))
    fenced = FencedGateway(deps.gateway)
    health = ToolHealth(fenced)
    asks = [{"id": f.id, "title": f.title, "first_words": issue.first_words(f.action), "link": f.link}
            for f in findings if not f.tracked_key]
    messages = ({"role": "system", "content": prompts.tracker_system()},
                {"role": "user", "content": prompts.fence_threads(json.dumps(asks, ensure_ascii=False, indent=1))})
    result = await run_tool_loop(deps.llm, deps.model, messages, tools, health, deps.traceparent,
                                 max_iterations=TRACKER_LOOP_CALLS, is_offered=health.is_offered)
    data = load_json_object(result.text) if result.status == "completed" else None
    if data is None:
        log.info("tracker read ended %s without a usable answer", result.status)
        return TrackerRead(TrackerContext(family=family, dup_check_ok=False))
    return checked_read(data, fenced.seen_text, family)


def checked_read(data: Mapping, seen: str, family: str) -> TrackerRead:
    """Only teams, projects, labels and issues whose ids and names a tool returned this turn."""
    context = TrackerContext(family=family, teams=_named(data.get("teams"), seen),
                             projects=_named(data.get("projects"), seen), labels=_named(data.get("labels"), seen),
                             dup_check_ok=True)
    return TrackerRead(context, _issues(data.get("issues"), seen))


def _named(raw, seen: str) -> tuple[Named, ...]:
    items = [Named(text_of(x.get("id")), text_of(x.get("name"))) for x in raw or () if isinstance(x, Mapping)]
    kept = [n for n in items if n.name and appears_in(n.name, seen) and (not n.id or appears_in(n.id, seen))]
    return tuple({n.name.casefold(): n for n in reversed(kept)}.values())[::-1]


def _issues(raw, seen: str) -> tuple[Existing, ...]:
    found = []
    for item in raw or ():
        if not isinstance(item, Mapping):
            continue
        key, url = text_of(item.get("key")), text_of(item.get("url"))
        if not key or not appears_in(key, seen):
            continue
        description = text_of(item.get("description"))
        found.append(Existing(key=key, url=url if appears_in(url, seen) else "", title=text_of(item.get("title")),
                              description=description if appears_in(description, seen) else ""))
    return tuple(found)


def mark_duplicates(findings: Sequence[Finding], issues: Sequence[Existing]) -> tuple[Finding, ...]:
    return tuple(_marked(f, issues) for f in findings)


def _marked(finding: Finding, issues: Sequence[Existing]) -> Finding:
    if finding.tracked_key:
        return finding
    hit = next((i for i in issues if is_duplicate(finding, i)), None)
    if hit is None:
        return finding
    return replace(finding, tracked_key=hit.key, tracked_url=hit.url or None, tracked_by="tracker")


def is_duplicate(finding: Finding, existing: Existing) -> bool:
    """The thread link (or thread id) in the issue description, or a title close to the proposed title or the
    action's first words."""
    for ref in (finding.link, finding.thread_ts):
        if len(ref) >= _MIN_REF_CHARS and appears_in(ref, existing.description):
            return True
    return any(issue.jaccard(existing.title, text) >= issue.DUPLICATE_JACCARD
               for text in (finding.title, issue.first_words(finding.action)) if text)


def hosts(issues: Sequence[Existing]) -> tuple[str, ...]:
    """The hosts of the tracker's own issue links seen this turn (a thread linking one is already tracked)."""
    found = (u.split("/")[2].casefold() for u in (i.url for i in issues) if u.count("/") >= 2)
    return tuple(dict.fromkeys(h for h in found if h))


async def create(deps: TurnDeps, tool: McpTool, approval: CreateApproval) -> WriteResult:
    messages = ({"role": "system", "content": prompts.create_system()},
                {"role": "user", "content": _create_request(tool, approval)})
    return await write_once(deps, tool, messages, _check(approval))


async def resume_create(deps: TurnDeps, tool: McpTool, approval: CreateApproval, loop: Mapping,
                        answer: str) -> WriteResult:
    return await resume_write(deps, tool, loop, answer, _check(approval))


def _check(approval: CreateApproval):
    return lambda name, arguments: create_guard.check(name, arguments, approval)


def _create_request(tool: McpTool, approval: CreateApproval) -> str:
    draft = approval.issue
    lines = [f"Create with the tool {tool.name}.", f"Title: {draft.title}"]
    if draft.team:
        lines.append(f"Team: {draft.team.name} (id {draft.team.id or 'unknown'})")
    if draft.project:
        lines.append(f"Project: {draft.project.name} (id {draft.project.id or 'unknown'})")
    if draft.labels:
        lines.append("Labels: " + ", ".join(f"{x.name} (id {x.id or 'unknown'})" for x in draft.labels))
    lines.append("Priority: leave unset")
    lines.append(f"Due date: {draft.due or 'leave unset'}")
    return "\n".join(lines) + "\nDescription:\n" + prompts.fence_approved(draft.description)


def created_ref(output: str) -> tuple[str, str]:
    """(issue key, url) from the create tool's own output; empty when it did not say."""
    key = ISSUE_KEY.search(output or "")
    urls = _URL.findall(output or "")
    url = next((u for u in urls if key and key.group(0) in u), urls[0] if urls else "")
    return (key.group(0) if key else "", url.rstrip(".,;"))
