"""The document steps: find the log by title, read its text (for duplicates and the changed-since check),
and make the one approved write. Each read is a small tool loop (tool schemas are only known at runtime)
whose JSON is checked in code: every id, title and link must appear in this turn's tool output."""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import block_guard
import prompts
from deps import TurnDeps
from discovery import Catalog
from fence import FencedGateway
from llmcall import load_json_object, text_of
from models import Doc
from nasiko_mcp import McpTool
from scan import all_dropped
from sender import WriteResult, resume_write, write_once
from textcheck import appears_in, normalise
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("decision_log.docs")

FIND_LOOP_CALLS = 3
READ_LOOP_CALLS = 3
MAX_DOCS = 8
DEFAULT_TITLE_WORD = "decision"
_URL = re.compile(r"https?://[^\s\"'<>)\]}]+")
_PAUSED = ("approval_pending", "authorization_required")


@dataclass(frozen=True)
class Found:
    docs: tuple[Doc, ...] = ()
    note: str = ""  # a coverage line when the documents could not be searched


@dataclass(frozen=True)
class Read:
    ok: bool
    text: str = ""
    version: str = ""
    note: str = ""


async def _loop(deps: TurnDeps, tools: Sequence[McpTool], system: str, user: str, calls: int):
    fenced = FencedGateway(deps.gateway)
    health = ToolHealth(fenced)
    messages = ({"role": "system", "content": system}, {"role": "user", "content": user})
    result = await run_tool_loop(deps.llm, deps.model, messages, tools, health, deps.traceparent,
                                 max_iterations=calls, is_offered=health.is_offered)
    return result, fenced.seen_text, health.dropped


def _unavailable(tools: Sequence[McpTool], dropped: Mapping[str, str], status: str) -> str:
    reason = all_dropped(tools, dropped)
    if reason is not None:
        return f"Doc: not available for your connection ({reason}), so I couldn't read your decision log."
    if status in _PAUSED:
        return "Doc: reading documents needs an approval for this agent, so I couldn't read your decision log."
    return ""


async def find(deps: TurnDeps, catalog: Catalog, title: str | None) -> Found:
    tools = catalog.doc_read_tools()
    if not tools:
        return Found(note=prompts.NO_DOC_TOOL_NOTE)
    wanted = title or "decision log"
    result, seen, dropped = await _loop(deps, tools, prompts.find_system(wanted), f"Find the document: {wanted}",
                                        FIND_LOOP_CALLS)
    note = _unavailable(tools, dropped, result.status)
    if note:
        return Found(note=note)
    data = load_json_object(result.text) or {}
    raw = [d for d in data.get("docs") or () if isinstance(d, Mapping)] if isinstance(data, Mapping) else []
    return Found(docs=check_docs(raw, seen, title))


def check_docs(raw: Sequence[Mapping[str, Any]], seen: str, title: str | None) -> tuple[Doc, ...]:
    """Docs whose id and title a tool returned and whose title matches what was asked, unique by id."""
    kept: dict[str, Doc] = {}
    for item in raw:
        doc_id, name, link = text_of(item.get("id")), text_of(item.get("title")), text_of(item.get("link"))
        if not (appears_in(doc_id, seen) and appears_in(name, seen)) or not title_matches(name, title):
            continue
        kept.setdefault(doc_id, Doc(title=name, id=doc_id, link=link if appears_in(link, seen) else "",
                                    state="found"))
    return tuple(kept.values())[:MAX_DOCS]


def title_matches(name: str, title: str | None) -> bool:
    have = normalise(name)
    if not title:
        return DEFAULT_TITLE_WORD in have
    want = normalise(title)
    return want in have or have in want


async def read(deps: TurnDeps, catalog: Catalog, doc: Doc) -> Read:
    tools = catalog.doc_read_tools()
    if not tools:
        return Read(ok=False, note=prompts.NO_DOC_TOOL_NOTE)
    result, seen, dropped = await _loop(deps, tools, prompts.read_system(doc.id, doc.title),
                                        f"Read the document {doc.id}.", READ_LOOP_CALLS)
    note = _unavailable(tools, dropped, result.status)
    if note or not seen:
        return Read(ok=False, note=note or f'Doc: I couldn\'t read "{doc.title}", so duplicates aren\'t marked.')
    data = load_json_object(result.text) or {}
    modified = text_of(data.get("modified")) if isinstance(data, Mapping) else ""
    return Read(ok=True, text=seen, version=version_of(seen, modified))


def version_of(text: str, modified: str = "") -> str:
    """The document's modified time when a tool returned one, else a hash of the text read."""
    if modified and appears_in(modified, text):
        return f"modified:{modified}"
    return "sha256:" + hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()[:16]


def _messages(approval: block_guard.WriteApproval) -> tuple[dict, ...]:
    if approval.kind == block_guard.CREATE:
        request = prompts.create_request(approval.tool, approval.title, approval.block)
    else:
        request = prompts.append_request(approval.tool, approval.doc_id, approval.title, approval.block)
    return ({"role": "system", "content": prompts.WRITE_SYSTEM}, {"role": "user", "content": request})


async def write(deps: TurnDeps, tool: McpTool, approval: block_guard.WriteApproval) -> WriteResult:
    return await write_once(deps, tool, _messages(approval), lambda n, a: block_guard.check(n, a, approval))


async def resume(deps: TurnDeps, tool: McpTool, approval: block_guard.WriteApproval, loop: Mapping[str, Any],
                 answer: str) -> WriteResult:
    return await resume_write(deps, tool, loop, answer, lambda n, a: block_guard.check(n, a, approval))


def link_in(output: str, fallback: str = "") -> str:
    match = _URL.search(output or "")
    return match.group(0) if match else fallback
