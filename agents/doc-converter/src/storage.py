"""The gateway path: find each named file, check its size, download it. Fixed order, no model.

One `Storage` per turn: it holds the catalog, the tool-health wrapper (a tool whose error says the
capability is unavailable is dropped; an identical failed call never reaches the gateway twice), the
record of tool output (a download link is fetched only if it appears there), and the call budget."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, replace
from urllib.parse import parse_qs, unquote, urlsplit

import fetched
import listing
import sources
import toolargs
from deps import TurnDeps
from discovery import Catalog
from fence import FencedGateway, raw_text
from listing import Entry
from models import Failure
from nasiko_mcp import (
    ApprovalPending,
    AuthorizationRequired,
    McpGatewayError,
    McpTool,
    ToolDenied,
    ToolOk,
)
from netguard import GuardError, SafeFetcher
from tool_health import ToolHealth, failure_text

log = logging.getLogger("doc_converter.storage")

CALL_TIMEOUT_SECS = 60.0
MAX_TOOL_CALLS = 18
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_CHOICES = 10
UNREACHABLE_LINK = ("the storage tool returned a download link this agent could not reach; paste the content or "
                    "send the file through the API")
_DRIVE_ID = re.compile(r"/d/([A-Za-z0-9_-]{10,})")


class CallFailed(Exception):
    """A tool call that produced no usable result; the message is the user-facing reason."""


@dataclass(frozen=True)
class Found:
    entries: tuple[Entry, ...] = ()
    failure: Failure | None = None


@dataclass(frozen=True)
class Download:
    name: str
    data: bytes
    mime: str | None


class Storage:
    def __init__(self, deps: TurnDeps, catalog: Catalog, place: str | None):
        self.deps = deps
        self.catalog = catalog
        self.place = place
        self.record = FencedGateway(deps.gateway)
        self.health = ToolHealth(self.record)
        self.calls = 0
        self.fetcher = deps.fetcher or SafeFetcher()

    async def call(self, tool: McpTool, args: dict) -> str:
        if self.calls >= MAX_TOOL_CALLS:
            raise CallFailed("the tool-call budget for this turn is used up; ask for fewer files at once")
        self.calls += 1
        try:
            result = await asyncio.wait_for(self.health.call_tool(tool.name, args, self.deps.traceparent),
                                            timeout=CALL_TIMEOUT_SECS)
        except TimeoutError as e:
            raise CallFailed("the storage tool did not answer within 60 seconds") from e
        except McpGatewayError as e:
            raise CallFailed(f"the tool gateway failed ({type(e).__name__})") from e
        if isinstance(result, AuthorizationRequired):
            raise CallFailed("the storage connection needs you to connect or re-authorize it first")
        if isinstance(result, ApprovalPending):
            raise CallFailed("the storage tool asked for approval; reads should be allowed for this agent")
        if isinstance(result, ToolDenied):
            raise CallFailed(f"the storage tool was blocked ({result.message[:120]})")
        error = failure_text(result) if isinstance(result, ToolOk) else None
        if error is not None:
            raise CallFailed(f"the storage tool returned an error ({error[:120]})")
        return raw_text(result)

    def places(self) -> tuple[str, ...]:
        return (self.place,) if self.place else sources.PLACES

    def offered(self, step: str, place: str | None) -> tuple[McpTool, ...]:
        return tuple(t for t in self.catalog.tools(place, step) if self.health.is_offered(t.name))

    async def find(self, request: dict) -> Found:
        link = request.get("link")
        name = _name_in_link(link) if link else (request.get("query") or "")
        if not name and not link:
            return Found(failure=Failure(name="(no name)", reason="no file name was given"))
        last = Found(failure=Failure(name=name or link, reason="no search tool is available for this storage"))
        for place in self.places():
            if not self.catalog.tools(place, "find") and not self.catalog.tools(place, "meta"):
                continue
            found = (self._from_link(link, place) if link else None) or await self._search(name, place)
            if found.entries:
                return found
            last = found
        return last

    def _from_link(self, link: str, place: str) -> Found | None:
        if sources.place_of_link(link) not in (place, None):
            return None
        parts = urlsplit(link)
        match = _DRIVE_ID.search(parts.path)
        ident = match.group(1) if match else (parse_qs(parts.query).get("id") or [None])[0]
        return Found((Entry(id=ident, name=ident, place=place),)) if ident else None

    async def _search(self, name: str, place: str) -> Found:
        reason = ("the search tool isn't available for your connection" if self.catalog.tools(place, "find")
                  else "no search tool is available for this storage")
        for tool in self.offered("find", place)[:2]:
            modes = (True, False) if toolargs.takes_expression(tool) else (True,)
            for exact in modes:
                args = toolargs.search_args(tool, name, exact)
                if args is None:
                    break
                try:
                    found = listing.entries(await self.call(tool, args))
                except CallFailed as e:
                    reason = str(e)
                    break
                matches = listing.matches(found, name)
                if matches:
                    return Found(tuple(replace(m, place=place) for m in matches))
                reason = f"no file named \"{name}\" was found"
            if reason.startswith("no file named"):
                break
        return Found(failure=Failure(name=name, reason=reason))

    async def describe(self, entry: Entry) -> Entry:
        """Fill size/type from the metadata tool when the search result lacked them."""
        if entry.size is not None and entry.mime and entry.name != entry.id:
            return entry
        for tool in self.offered("meta", entry.place)[:1]:
            args = toolargs.id_args(tool, entry.id)
            if args is None:
                continue
            try:
                found = listing.entries(await self.call(tool, args))
            except CallFailed as e:
                log.info("metadata for %s failed: %s", entry.id, e)
                return entry
            return listing.merge(entry, next((f for f in found if f.id == entry.id), found[0] if found else None))
        return entry

    async def download(self, entry: Entry) -> Download:
        export = toolargs.export_for(entry.mime)
        tools = sorted(self.offered("fetch", entry.place), key=lambda t: ("export" in t.name.casefold()) != bool(export))
        reason = "no download tool is available for this storage"
        for tool in tools[:2]:
            args = toolargs.id_args(tool, entry.id, entry.mime)
            if args is None:
                continue
            try:
                payload = fetched.read_result(await self.call(tool, args))
            except fetched.TooLarge as e:
                raise CallFailed("is larger than 20 MB; the limit is 20 MB") from e
            if payload is None:
                reason = "the storage tool returned no file content"
                continue
            name = entry.name if not export or entry.name.endswith(f".{export[1]}") else f"{entry.name}.{export[1]}"
            return Download(name, await self._bytes(payload), payload.mime or entry.mime)
        raise CallFailed(reason)

    async def _bytes(self, payload: fetched.Payload) -> bytes:
        if payload.data is not None:
            return payload.data
        if payload.text is not None:
            return payload.text.encode("utf-8")
        if payload.url not in self.record.seen_text:
            raise CallFailed(UNREACHABLE_LINK)
        try:
            return await self.fetcher.fetch(payload.url)
        except GuardError as e:
            log.warning("download link refused or failed: %s", e)
            raise CallFailed(UNREACHABLE_LINK) from e


def _name_in_link(link: str) -> str:
    tail = urlsplit(link).path.rstrip("/").rsplit("/", 1)[-1]
    return unquote(tail)
