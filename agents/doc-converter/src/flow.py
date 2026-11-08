"""The turn's steps, on one per-turn Scope (never persisted): intake -> [ask] -> locate -> fetch ->
convert -> render. Each step returns the next route, or "" to end the turn."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field, replace

import coverage
import documents
import parse
import prompts
import render as rendering
import smalltalk
import sources
from deps import TurnDeps
from discovery import discover
from documents import Blob
from hitl import Job, Pause
from inputs import Incoming
from listing import Entry
from models import Doc, Failure, envelope
from storage import MAX_CHOICES, MAX_FILE_BYTES, CallFailed, Storage

log = logging.getLogger("doc_converter.flow")

MAX_CONCURRENT_FETCH = 3
_BEYOND = re.compile(r"\b(summari[sz]e|summary|translat\w*|explain|tl;?dr|key points|shorten|rewrite|analy[sz]e"
                     r"|tell me what|what does it say)\b", re.IGNORECASE)


@dataclass
class Scope:
    deps: TurnDeps
    incoming: Incoming = field(default_factory=lambda: Incoming(""))
    job: Job = field(default_factory=Job)
    blobs: list[Blob] = field(default_factory=list)
    resolved: list[Entry] = field(default_factory=list)
    docs: list[Doc] = field(default_factory=list)
    failed: list[Failure] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    storage: Storage | None = None
    pause: Pause | None = None
    reply: str = ""
    data: dict | None = None
    pages: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    ambiguous: bool = False


async def intake(scope: Scope) -> str:
    inc = scope.incoming
    if not scope.deps.traceparent:
        scope.reply = prompts.NO_TRACE_CONTEXT
        return ""
    scope.failed = list(inc.refused)
    scope.blobs = documents.from_incoming(inc)
    base = Job(wants_json=inc.wants_json, pages=inc.pages, sheet=inc.sheet, beyond=bool(_BEYOND.search(inc.request)))
    scope.job = base
    if inc.named:
        place = next((sources.place_of_link(n["link"]) for n in inc.named if n.get("link")), None)
        return _start(scope, replace(base, place=place, requests=inc.named))
    found = parse.literal(inc.request)
    if (inc.has_content or inc.refused) and not found.files:
        return "convert"
    if parse.needs_model(found):
        try:
            found = parse.merge(found, await parse.resolve(scope.deps, inc.request))
        except parse.ParseError as e:
            log.warning("parse call failed: %s", e)
            return _end(scope, prompts.MODEL_UNAVAILABLE if not inc.has_content else None)
    if found.status == "off_topic":
        return "convert" if inc.has_content else _end(scope, smalltalk.OFF_TOPIC_REPLY)
    if not found.files:
        if inc.has_content:
            return "convert"
        return _end(scope, prompts.PASTE_NEXT if found.place == "pasted" else prompts.NO_FILE)
    place = found.place if found.place in sources.PLACES else None
    return _start(scope, replace(base, place=place, requests=found.files))


def _end(scope: Scope, reply: str | None) -> str:
    if reply is None:
        return "convert"
    scope.reply = reply
    return ""


def _start(scope: Scope, job: Job) -> str:
    scope.job = job
    if job.place is None and not scope.incoming.has_content:
        scope.pause = Pause("source", job)
        return ""
    return "locate"


async def locate(scope: Scope) -> str:
    job = scope.job
    catalog, failure = await discover(scope.deps, job.place)
    scope.resolved = [Entry.from_dict(e) for e in job.resolved]
    scope.failed += [Failure(**f) for f in job.failed]
    if failure is not None:
        names = [r.get("query") or r.get("link") or "file" for r in job.requests]
        scope.failed += [Failure(name=n, reason="not fetched (see below)") for n in names]
        scope.failed += [Failure(name=e.name, reason="not fetched (see below)") for e in scope.resolved]
        scope.resolved = []
        scope.notes.append(failure)
        return "convert"
    scope.storage = Storage(scope.deps, catalog, job.place)
    remaining = list(job.requests)
    while remaining:
        request = remaining.pop(0)
        found = await scope.storage.find(request)
        if found.failure is not None:
            scope.failed.append(found.failure)
        elif len(found.entries) == 1:
            scope.resolved.append(found.entries[0])
        elif len(found.entries) > MAX_CHOICES:
            name = request.get("query") or request.get("link") or "file"
            scope.failed.append(Failure(name=name, reason=f"{len(found.entries)} files match; give a more exact name"))
            scope.ambiguous = True
        else:
            pending = replace(job, requests=tuple(remaining), resolved=tuple(e.to_dict() for e in scope.resolved),
                              failed=tuple(f.model_dump() for f in scope.failed))
            query = request.get("query") or request.get("link") or ""
            scope.pause = Pause("match", pending, query, tuple(e.to_dict() for e in found.entries))
            return ""
    return "fetch"


async def fetch(scope: Scope) -> str:
    slots = asyncio.Semaphore(MAX_CONCURRENT_FETCH)

    async def one(entry: Entry) -> Blob | Failure:
        async with slots:
            return await _fetch_one(scope.storage, entry)

    outcomes = await asyncio.gather(*(one(e) for e in scope.resolved))
    scope.blobs += [o for o in outcomes if isinstance(o, Blob)]
    scope.failed += [o for o in outcomes if isinstance(o, Failure)]
    return "convert"


async def _fetch_one(storage: Storage, entry: Entry) -> Blob | Failure:
    try:
        entry = await storage.describe(entry)
        if entry.size is not None and entry.size > MAX_FILE_BYTES:
            return Failure(name=entry.name, reason=f"is {entry.size / (1024 * 1024):.1f} MB; the limit is 20 MB")
        download = await storage.download(entry)
    except CallFailed as e:
        return Failure(name=entry.name, reason=str(e))
    return Blob(download.name, entry.place or "drive", download.data, download.mime or "")


async def convert(scope: Scope, converter) -> str:
    options = {"pages": scope.job.pages, "sheet": scope.job.sheet}
    outcomes = await asyncio.gather(*(documents.convert(converter, b, options) for b in scope.blobs))
    scope.docs = [o for o in outcomes if isinstance(o, Doc)]
    scope.failed += [o for o in outcomes if isinstance(o, Failure)]
    return "render"


def render(scope: Scope) -> str:
    if scope.storage is not None:
        looked = {p: scope.storage.catalog.tools(p, "find") for p in scope.storage.places()}
        scope.notes += coverage.notes(scope.job.place, looked, scope.storage.health.dropped)
    env = envelope(scope.docs, scope.failed, scope.ambiguous)
    rendered = rendering.render(env, scope.notes, scope.job.beyond)
    scope.data = env.model_dump()
    scope.pages, scope.warnings = rendered.pages, rendered.warnings
    scope.reply = rendering.json_reply(env) if scope.job.wants_json else rendered.pages[0]
    return ""
