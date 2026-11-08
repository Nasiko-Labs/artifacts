"""The optional fetch: when nothing is pasted, find a connected app-review tool and read one app's recent
reviews through it (one tool loop, then one repair or evidence read). Every fetched review must carry an id
and body that appear in this turn's tool output; anything else is dropped."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

import checks
import coverage
import discovery
import normalise
import prompts
import replies
import versions
from deps import TurnDeps
from fence import FencedGateway
from llm import LlmError, call, load
from nasiko_mcp import McpGatewayError
from records import Review
from tool_health import ToolHealth
from tool_loop import run_tool_loop

log = logging.getLogger("review_miner.fetch")
_APP = re.compile(r"\breviews?\s+(?:for|of|about)\s+(.+?)(?:\s+(?:in|over|from|for)?\s*(?:the\s+)?"
                  r"(?:last|past)\s+(?:\d+\s+days?|week|month))?\s*[.?!]*$", re.IGNORECASE)
NAME_THE_APP = "Name the app to fetch, for example \"reviews for Acme Notes last 30 days\", or paste the reviews."


class _Fetched(BaseModel):
    id: str
    body: str
    rating: object = None
    version: object = None
    date: object = None
    store: object = None
    title: object = None


class _Answer(BaseModel):
    reviews: list[_Fetched]


@dataclass(frozen=True)
class Fetched:
    reviews: tuple[Review, ...] = ()
    coverage: tuple[str, ...] = ()
    reply: str | None = None   # set when nothing was fetched: the fixed reply for this turn
    dropped: int = 0


def app_named(text: str) -> str | None:
    found = _APP.search(" ".join((text or "").split()))
    name = found.group(1).strip(" \"'") if found else ""
    return name or None


def _validate(raw: object) -> _Answer | None:
    try:
        return _Answer.model_validate(raw)
    except ValidationError:
        return None


def _reviews(answer: _Answer, seen: str, app: str, start: int) -> tuple[list[Review], int]:
    kept: list[Review] = []
    for r in answer.reviews:
        if not r.id or r.id not in seen or not checks.quote_ok(r.body, seen, len(seen) + 1):
            continue
        kept.append(Review(id=f"R{start + len(kept)}", app=app, body=r.body.strip(), rating=normalise.rating(r.rating),
                           version=versions.normalise(r.version), date=normalise.iso_date(r.date),
                           store=normalise.store(r.store), title=str(r.title or "").strip(), source="fetched"))
    return kept, len(answer.reviews) - len(kept)


async def fetch(deps: TurnDeps, query: str, start: int) -> Fetched:
    if deps.gateway is None:
        return Fetched(reply=f"{coverage.MISSING}\n\n{replies.FORMAT_HELP}")
    try:
        catalog = await discovery.discover(deps)
    except McpGatewayError as e:
        log.warning("tools/list failed: %s", e)
        return Fetched(reply=f"{replies.GATEWAY_UNREACHABLE}\n\n{replies.FORMAT_HELP}")
    tools = catalog.read_tools()
    if not tools:
        return Fetched(reply=f"{coverage.MISSING}\n\n{replies.FORMAT_HELP}")
    app = app_named(query)
    if app is None:
        return Fetched(reply=f"{NAME_THE_APP}\n\n{replies.FORMAT_HELP}")
    health = ToolHealth(deps.gateway)
    fenced = FencedGateway(health, deps.config.fetch_max_tool_calls)
    messages = ({"role": "system", "content": prompts.fetch_system(app)}, {"role": "user", "content": f"Reviews for {app}."})
    try:
        result = await run_tool_loop(deps.llm, deps.model, messages, tools, fenced, deps.traceparent,
                                     max_iterations=deps.config.fetch_max_iterations, is_offered=health.is_offered)
    except McpGatewayError as e:
        log.warning("fetch loop failed: %s", e)
        return Fetched(reply=f"{replies.GATEWAY_UNREACHABLE}\n\n{replies.FORMAT_HELP}")
    deps.budget.calls += sum(1 for m in result.messages if m.get("role") == "assistant")
    gap = coverage.line(tools, health.dropped)
    answer = _validate(load(result.text)) or await _evidence_read(deps, app, fenced.seen_text)
    reviews, dropped = _reviews(answer, fenced.seen_text, app, start) if answer else ([], 0)
    if not reviews:
        lines = gap or "The review tool returned no reviews I could verify for that app."
        return Fetched(coverage=(lines,), reply=f"{lines}\n\n{replies.FORMAT_HELP}")
    return Fetched(tuple(reviews), (gap,) if gap else (), None, dropped)


async def _evidence_read(deps: TurnDeps, app: str, seen: str) -> _Answer | None:
    """One tool-less repair: the contract over everything the tools returned."""
    if not seen:
        return None
    messages = ({"role": "system", "content": prompts.fetch_system(app)},
                {"role": "user", "content": prompts.fence(seen[:24_000])})
    try:
        return _validate(load(await call(deps, messages, json_mode=True)))
    except LlmError as e:
        log.warning("fetch evidence read failed: %s", e)
        return None
