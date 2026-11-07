"""The doc step: find ONE document by title, link or id and read it. A bounded tool loop over document read
tools; the model names the document, and code takes its text from the read tool's own output (never echoed
by the model), caps it, and checks the id/title appear in the tool output."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import coverage
import prompts
import replies
from deps import TurnDeps
from discovery import discover
from fence import SeenCall
from llmcall import load_json_object, text_of
from reader import TurnReader
from records import Spec
from request import Ask
from textcheck import appears_in

DOC_LOOP_CALLS = 3  # plus one JSON repair
_SEARCHY = ("search", "list", "find", "query")


@dataclass(frozen=True)
class DocOutcome:
    spec: Spec | None = None
    reply: str | None = None
    notes: tuple[str, ...] = ()
    salvaged: bool = False


@dataclass(frozen=True)
class _Found:
    status: str
    id: str = ""
    url: str = ""
    title: str = ""


def parse_doc(text: str) -> _Found | None:
    data = load_json_object(text)
    if data is None or data.get("status") not in ("ok", "not_found"):
        return None
    doc = data.get("doc") if isinstance(data.get("doc"), Mapping) else {}
    return _Found(data["status"], text_of(doc.get("id")), text_of(doc.get("url")), text_of(doc.get("title")))


async def doc_step(deps: TurnDeps, reader: TurnReader, ask: Ask) -> DocOutcome:
    if ask.pasted:
        return _pasted(deps, ask)
    catalog, failure = await discover(deps, "doc")
    if failure is not None:
        return DocOutcome(reply=failure)
    tools = catalog.read_tools("docs")
    if not coverage.assess("docs", tools).ok:
        return DocOutcome(reply=replies.NO_DOC_TOOL)
    run = await reader.run_json(deps, tools, prompts.doc_system(ask.link), prompts.fence_ask(ask.text),
                                DOC_LOOP_CALLS, parse_doc)
    status = coverage.assess("docs", tools, reader.health.dropped)
    if not status.ok:
        return DocOutcome(reply=f"{replies.NO_DOC_TOOL}\n\n{coverage.line(status)}")
    if run.status == "paused":
        return DocOutcome(reply=replies.READ_NEEDS_APPROVAL)
    found = run.parsed
    if found is None:
        return DocOutcome(reply=replies.STEP_LIMIT_NOTE if run.status in ("limit", "max_iterations")
                          else replies.UNREADABLE_ANSWER)
    if found.status != "ok" or not found.id or not appears_in(found.id, reader.seen_text):
        return DocOutcome(reply=replies.DOC_NOT_FOUND)
    text = document_text(reader.calls, found.id)
    if not text.strip():
        return DocOutcome(reply=replies.DOC_NOT_FOUND)
    title = found.title if appears_in(found.title, reader.seen_text) else "untitled document"
    url = found.url if appears_in(found.url, reader.seen_text) else ""
    return DocOutcome(spec=_capped(deps, Spec(title=title, text=text, id=found.id, url=url)), notes=run.notes,
                      salvaged=run.salvaged)


def _pasted(deps: TurnDeps, ask: Ask) -> DocOutcome:
    lines = [ln.strip("# ").strip() for ln in ask.pasted.splitlines() if ln.strip()]
    title = (lines[0] if lines else "Pasted spec")[:80]
    return DocOutcome(spec=_capped(deps, Spec(title=title, text=ask.pasted, source=ask.pasted_source or "pasted")))


def _capped(deps: TurnDeps, spec: Spec) -> Spec:
    limit = deps.config.max_spec_chars
    if len(spec.text) <= limit:
        return spec
    return Spec(title=spec.title, text=spec.text[:limit], id=spec.id, url=spec.url, source=spec.source,
                cut_chars=len(spec.text) - limit)


def document_text(calls: tuple[SeenCall, ...], doc_id: str) -> str:
    """The document's text: the longest text value in a successful read call naming the id (else any read)."""
    reads = [c for c in calls if not c.is_error and not any(w in c.name.casefold() for w in _SEARCHY)]
    naming = [c for c in reads if doc_id in json.dumps(dict(c.arguments), default=str)]
    bodies = [_longest_text(c.body) for c in (naming or reads)]
    return max(bodies, key=len, default="")


def _longest_text(body: str) -> str:
    try:
        data: Any = json.loads(body)
    except ValueError:
        return body
    strings = list(_strings(data))
    return max(strings, key=len, default="")


def _strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _strings(v)
