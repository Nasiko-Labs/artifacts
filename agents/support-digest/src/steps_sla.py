"""The SLA step: the desk's SLA policies by priority, read by a short tool loop and verified in code. When no
tool exposes them (or nothing verifiable comes back) the defaults in config.py are used, and the digest
says so."""

from __future__ import annotations

import logging

import prompts
import records
import sla
import verify
from deps import TurnDeps
from discovery import discover
from reader import TurnReader

log = logging.getLogger("support_digest.sla")

SLA_LOOP_CALLS = 2


async def sla_step(deps: TurnDeps, turn: TurnReader) -> sla.Targets:
    catalog, failure = await discover(deps, "sla")
    if failure is not None:
        return sla.targets(())
    tools = catalog.source_tools("desk")
    if not tools:
        return sla.targets(())
    before = len(turn.seen_text)
    run = await turn.run_json(deps, tools, prompts.SLA_SYSTEM, "Read the SLA policies.", SLA_LOOP_CALLS,
                              deps.config.sla_tool_calls, records.parse_policies)
    if not run.parsed:
        return sla.targets(())
    policies = verify.check_policies(run.parsed, turn.seen_text[before:])
    log.info("SLA policies verified: %s", [p.priority for p in policies])
    return sla.targets(policies)
