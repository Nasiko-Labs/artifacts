"""The read steps: find + read the contract, extract its terms per chunk, read the billing setup. Each tool
step is a bounded tool loop whose JSON answer is checked in code. Also the per-source read side (`Reader`)
and the tool-less single call (`ask_once`)."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import billing
import coverage
import prompts
import terms as t
from deps import TurnDeps
from discovery import Catalog, Source, name_tokens
from fence import FencedGateway
from nasiko_mcp import McpTool
from request import Request
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("deal_reconciler.steps")

CONTRACT_LOOP_CALLS = 4     # plus one JSON repair
BILLING_LOOP_CALLS = 6      # plus one JSON repair
CHUNK_CHARS = 6_000
MAX_CHUNKS = 3
MIN_CONTRACT_CHARS = 40
MAX_CANDIDATES = 10
_PAUSED = ("approval_pending", "authorization_required")
_LISTING_WORDS = frozenset({"search", "list", "find", "query"})


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1)
    return result.text.strip()


@dataclass(frozen=True)
class Reader:
    """One source's read side for this turn: the tools offered to the model, the fence adapter that
    records their output, and the failure tracker in front of it."""

    source: str
    tools: tuple[McpTool, ...]
    fenced: FencedGateway
    health: ToolHealth
    helpers: frozenset[str] = frozenset()

    async def loop(self, deps: TurnDeps, messages: Sequence[Mapping[str, Any]], max_iterations: int) -> LoopResult:
        return await run_tool_loop(deps.llm, deps.model, messages, self.tools, self.health, deps.traceparent,
                                   max_iterations=max_iterations, is_offered=self.health.is_offered)

    @property
    def status(self) -> coverage.SourceStatus:
        return coverage.assess(self.source, self.tools, self.health.dropped, self.helpers)

    @property
    def seen_text(self) -> str:
        return self.fenced.seen_text


def reader_for(deps: TurnDeps, catalog: Catalog | None, source: Source) -> Reader:
    """The read tools whose own name or description places them in this source, plus the recovery helper."""
    own = catalog.for_source(source) if catalog else ()
    recover = catalog.recover if catalog else None
    tools = (*own, recover) if own and recover else own
    fenced = FencedGateway(deps.gateway)
    helpers = frozenset({recover.name}) if recover else frozenset()
    return Reader(source, tuple(tools), fenced, ToolHealth(fenced), helpers)


async def _repair(deps: TurnDeps, system: str, query: str, previous: str, seen_text: str) -> str:
    """One tool-less call that restates the answer as JSON from this turn's (fenced) tool output."""
    log.info("answer was not the agreed JSON; asking once for a rewrite")
    evidence = prompts.fence(seen_text) if seen_text else "(no tool results this turn)"
    return await ask_once(deps, (
        {"role": "system", "content": system},
        {"role": "user", "content": query},
        {"role": "user", "content": f"Tool results from this turn:\n{evidence}\n\nYour previous answer:\n"
                                    f"{prompts.fence(previous)}\n\n{prompts.REPAIR_INSTRUCTION}"},
    ))


# --- contract -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ContractRead:
    status: str            # ok | multiple | not_found | not_text | unavailable | paused | unreadable | off_topic
    text: str = ""
    title: str | None = None
    link: str | None = None
    candidates: tuple[Mapping[str, str], ...] = ()
    salvaged: bool = False


async def read_contract(deps: TurnDeps, reader: Reader, request: Request, document_id: str | None = None,
                        query: str = "") -> ContractRead:
    system = prompts.contract_system(deps.now, request.document or request.customer, document_id)
    user = query or (request.document or request.customer or "the contract")
    result = await reader.loop(deps, ({"role": "system", "content": system}, {"role": "user", "content": user}),
                               CONTRACT_LOOP_CALLS)
    if not reader.status.ok:
        return ContractRead("unavailable")
    if result.status in _PAUSED:
        return ContractRead("paused")
    hit_limit = result.status == "max_iterations"
    data = None if hit_limit else t.json_object(result.text)
    if not isinstance(data, Mapping) and reader.seen_text:
        data = t.json_object(await _repair(deps, system, user, result.text, reader.seen_text))
    text = contract_text(reader.fenced.records)
    if not isinstance(data, Mapping):
        return ContractRead("ok", text, salvaged=True) if _readable(text) else ContractRead("unreadable")
    return _contract_outcome(data, text, reader.seen_text, hit_limit)


def _contract_outcome(data: Mapping[str, Any], text: str, seen_text: str, hit_limit: bool) -> ContractRead:
    status = str(data.get("status") or "")
    if status == "off_topic":
        return ContractRead("off_topic")
    if status == "multiple":
        found = [c for c in data.get("candidates") or () if isinstance(c, Mapping)
                 and _seen(c.get("id"), seen_text) and _seen(c.get("title"), seen_text)]
        unique = tuple({str(c["id"]): {"id": str(c["id"]), "title": str(c["title"])} for c in found}.values())
        if len(unique) > 1:
            return ContractRead("multiple", candidates=unique[:MAX_CANDIDATES])
        if len(unique) == 1 and not _readable(text):
            return ContractRead("multiple", candidates=unique)
    document = data.get("document") if isinstance(data.get("document"), Mapping) else {}
    title = str(document["title"]) if _seen(document.get("title"), seen_text) else None
    link = str(document["link"]) if _seen(document.get("link"), seen_text) else None
    if _readable(text):
        return ContractRead("ok", text, title, link, salvaged=hit_limit)
    if status == "not_text" or (title and status == "ok"):
        return ContractRead("not_text", title=title)
    return ContractRead("not_found")


def contract_text(records: Sequence[tuple[str, str]]) -> str:
    """The text of the read calls (not the search listings), JSON bodies flattened to their strings."""
    reads = [body for name, body in records if not _LISTING_WORDS & set(name_tokens(name))]
    bodies = reads or [body for _, body in records]
    return "\n".join(_flatten(body) for body in bodies).strip()


def _flatten(body: str) -> str:
    try:
        data = json.loads(body)
    except ValueError:
        return body
    return "\n".join(_strings(data))


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _readable(text: str) -> bool:
    return len(text) >= MIN_CONTRACT_CHARS and any(ch.isdigit() for ch in text)


def _seen(value: Any, seen_text: str) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value in seen_text


# --- extraction -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Extraction:
    terms: t.ContractTerms
    chunks: int
    read_chars: int
    total_chars: int
    failed_chunks: int = 0

    @property
    def complete(self) -> bool:
        return self.read_chars >= self.total_chars and not self.failed_chunks


def chunks_of(text: str, size: int = CHUNK_CHARS, limit: int = MAX_CHUNKS) -> tuple[str, ...]:
    """Up to `limit` chunks of at most `size` chars, cut at a line or word boundary when one is near."""
    found: tuple[str, ...] = ()
    rest = text
    while rest and len(found) < limit:
        if len(rest) <= size:
            return (*found, rest)
        cut = max(rest.rfind("\n", size // 2, size), rest.rfind(" ", size // 2, size))
        cut = cut if cut > 0 else size
        found, rest = (*found, rest[:cut]), rest[cut:].lstrip()
    return found


async def extract(deps: TurnDeps, text: str) -> Extraction:
    parts = chunks_of(text)
    checked: tuple[t.ContractTerms, ...] = ()
    failed = 0
    for n, chunk in enumerate(parts, start=1):
        answer = await ask_once(deps, ({"role": "system", "content": prompts.extract_system(n, len(parts))},
                                       {"role": "user", "content": prompts.fence_contract(chunk)}))
        raw = t.parse_extraction(answer)
        if raw is None:
            failed += 1
            continue
        checked = (*checked, t.check_chunk(raw, text))
    merged = t.merge(checked)
    if merged.dropped:
        log.info("post-check dropped %s (quote or value not in the contract)", ", ".join(merged.dropped))
    return Extraction(merged, len(parts), sum(len(p) for p in parts), len(text), failed)


# --- billing --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class BillingRead:
    status: str      # ok | multiple_customers | customer_not_found | unavailable | paused | unreadable | off_topic
    config: billing.BillingConfig | None = None
    candidates: tuple[Mapping[str, str | None], ...] = ()
    salvaged: bool = False


async def read_billing(deps: TurnDeps, reader: Reader, request: Request, contract: t.ContractTerms,
                       customer_id: str | None = None) -> BillingRead:
    customer = contract.get("customer")
    name = request.customer or (customer.value if customer else None)
    start = contract.get("start_date")
    system = prompts.billing_system(deps.now, name, request.email, customer_id,
                                    start.value.isoformat() if start else None)
    user = f"Read the billing setup for {customer_id or name or request.email or 'this customer'}."
    result = await reader.loop(deps, ({"role": "system", "content": system}, {"role": "user", "content": user}),
                               BILLING_LOOP_CALLS)
    if not reader.status.ok:
        return BillingRead("unavailable")
    if result.status in _PAUSED:
        return BillingRead("paused")
    hit_limit = result.status == "max_iterations"
    parsed = None if hit_limit else billing.parse_answer(result.text, reader.seen_text)
    if parsed is None and reader.seen_text:
        parsed = billing.parse_answer(await _repair(deps, system, user, result.text, reader.seen_text),
                                      reader.seen_text)
    if parsed is None:
        return BillingRead("unreadable")
    if parsed.status == "multiple_customers" and len(parsed.candidates) > 1:
        return BillingRead("multiple_customers", candidates=parsed.candidates)
    if parsed.status == "off_topic":
        return BillingRead("off_topic")
    if parsed.status != "ok" or parsed.config is None or parsed.config.customer_id is None:
        return BillingRead("customer_not_found")
    return BillingRead("ok", parsed.config, salvaged=hit_limit)
