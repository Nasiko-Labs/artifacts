"""The three read lookups (repos, deploys, owner): bounded tool loops over the tools discovery found for one source,
each answer parsed against its JSON contract and post-checked verbatim against this turn's tool output.

Budgets (LLM calls): repos <= 3, deploys <= 6, owner <= 4, each including one tool-less repair call used only when
the loop's answer was not the agreed JSON (step-limit salvage). The model never computes a date: the deploy window
is given as exact dates."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, TypeVar

from pydantic import BaseModel

import checks
import coverage
import prompts
from correlate import LOOKAHEAD_DAYS, MAX_LAG_DAYS, Change
from deps import LLM_CALL_TIMEOUT_SECS, TurnDeps
from discovery import RECOVER_META, Catalog, Source
from fence import FencedGateway
from nasiko_mcp import McpTool
from records import ChangesOut, Channel, Owner, OwnerLookupOut, ReposOut, parse_model
from tool_health import ToolHealth
from tool_loop import LoopResult, run_tool_loop

log = logging.getLogger("spend_spike.steps")

REPOS_CALLS = 3
DEPLOYS_CALLS = 6
OWNER_CALLS = 4
_PAUSED = ("approval_pending", "authorization_required")
T = TypeVar("T", bound=BaseModel)


class NoTools:
    """The gateway for tool-less LLM calls; the loop never forwards when the catalog is empty."""

    async def call_tool(self, name: str, arguments: Mapping[str, Any], traceparent: str):
        raise AssertionError(f"tool-less call tried to forward {name!r}")


async def ask_once(deps: TurnDeps, messages: Sequence[Mapping[str, Any]]) -> str:
    """One tool-less LLM call through the same loop, so it carries the traceparent the same way."""
    result = await run_tool_loop(deps.llm, deps.model, messages, (), NoTools(), deps.traceparent, max_iterations=1,
                                 timeout=LLM_CALL_TIMEOUT_SECS)
    return result.text.strip()


@dataclass(frozen=True)
class Lookup[R]:
    value: R
    note: str | None = None
    ran: bool = False          # the source had a working tool and the loop ran


class Reader:
    """One turn's read side: the catalog, the fence adapter that records tool output, and the failure tracker."""

    def __init__(self, deps: TurnDeps, catalog: Catalog):
        self.deps = deps
        self.catalog = catalog
        self.fenced = FencedGateway(deps.gateway)
        self.health = ToolHealth(self.fenced)

    @property
    def seen(self) -> str:
        return self.fenced.seen_text

    def tools(self, source: Source) -> tuple[McpTool, ...]:
        return tuple(t for t in self.catalog.read_tools(source) if self.health.is_offered(t.name))

    async def ask(self, model: type[T], source: Source, system: str, user: str,
                  calls: int) -> tuple[T | None, str | None, bool]:
        """(parsed answer, coverage note, ran). At most `calls` LLM calls including the repair."""
        tools = self.tools(source)
        if not [t for t in tools if t.name != RECOVER_META]:
            return None, coverage.note(coverage.assess(source, (), {})), False
        messages = ({"role": "system", "content": system}, {"role": "user", "content": user})
        result = await run_tool_loop(self.deps.llm, self.deps.model, messages, tools, self.health,
                                     self.deps.traceparent, max_iterations=calls - 1,
                                     is_offered=self.health.is_offered, timeout=LLM_CALL_TIMEOUT_SECS)
        status = coverage.assess(source, tools, self.health.dropped, frozenset({RECOVER_META}))
        if not status.ok:
            return None, coverage.note(status), False
        if result.status in _PAUSED:
            return None, f"A {source} read tool is set to require approval, so that lookup was skipped.", False
        parsed = parse_model(model, result.text) if result.status == "completed" else None
        note = prompts.STEP_LIMIT_NOTE if result.status == "max_iterations" else None
        if parsed is None and self.seen:
            parsed = await self._repair(model, system, user, result)
        return parsed, note, True

    async def _repair(self, model: type[T], system: str, user: str, result: LoopResult) -> T | None:
        log.info("lookup answer was not the agreed JSON; asking once for a rewrite")
        messages = (
            {"role": "system", "content": system},
            {"role": "user", "content": user},
            {"role": "user", "content": f"Tool results from this turn:\n{prompts.fence(self.seen)}\n\nYour previous "
                                        f"answer:\n{prompts.fence(result.text)}\n\nRewrite it as ONLY the JSON object "
                                        "described in the system prompt, using only the tool results above."},
        )
        return parse_model(model, await ask_once(self.deps, messages))


async def find_repos(reader: Reader, terms: Sequence[str]) -> Lookup[tuple[str, ...]]:
    user = "Find the repositories for: " + ", ".join(terms)
    parsed, note, ran = await reader.ask(ReposOut, "code", prompts.repos_system(terms), user, REPOS_CALLS)
    return Lookup(checks.checked_repos(parsed, reader.seen), note, ran)


def deploy_window(onset: date) -> tuple[date, date]:
    return onset - timedelta(days=MAX_LAG_DAYS), onset + timedelta(days=LOOKAHEAD_DAYS)


async def find_changes(reader: Reader, repos: Sequence[str], onset: date) -> Lookup[tuple[Change, ...]]:
    since, until = deploy_window(onset)
    user = f"List the changes in {', '.join(repos)} between {since.isoformat()} and {until.isoformat()}."
    parsed, note, ran = await reader.ask(ChangesOut, "code", prompts.deploys_system(repos, since, until), user,
                                         DEPLOYS_CALLS)
    return Lookup(checks.checked_changes(parsed, reader.seen, repos), note, ran)


async def find_owner(reader: Reader, team: str | None,
                     logins: Sequence[str]) -> Lookup[tuple[Owner, tuple[Channel, ...]]]:
    user = "Find who to message: " + ", ".join([*([team] if team else []), *logins])
    parsed, note, ran = await reader.ask(OwnerLookupOut, "chat", prompts.owner_system(team, logins), user,
                                         OWNER_CALLS)
    return Lookup(checks.checked_owner(parsed, reader.seen, team), note, ran)
