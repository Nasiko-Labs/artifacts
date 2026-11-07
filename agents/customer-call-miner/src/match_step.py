"""The match step: look up each external domain's company in the CRM, with argument-template replay.

    loop (≤ 4 LLM calls) → replay the model's company-by-domain call for every domain it did not cover
    → replay its deal lookup for every company id it did not cover → one tool-less read of the evidence
    (only when something was replayed, or the loop's answer is unusable) → code checks every account.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

import accounts as acc
import prompts
import replay
from deps import TurnDeps
from reader import Reader, ask_once
from records import Account, load_json_object

log = logging.getLogger("call_miner.match")

MATCH_LOOP_CALLS = 4  # plus one evidence read = 5
REPLAY_CONCURRENCY = 4
EVIDENCE_CHARS = 40_000


@dataclass(frozen=True)
class MatchOutcome:
    accounts: tuple[Account, ...]
    replayed: int = 0


async def match_accounts(deps: TurnDeps, reader: Reader, domains: Sequence[str]) -> MatchOutcome:
    if not domains or not reader.tools:
        return MatchOutcome(())
    system = prompts.match_system(domains)
    messages = ({"role": "system", "content": system},
                {"role": "user", "content": "Look up these companies and their latest closed-won deals."})
    result = await reader.loop(deps, messages, MATCH_LOOP_CALLS)
    replayed = await _replay_domains(deps, reader, domains)
    replayed += await _replay_ids(deps, reader, domains)
    data = load_json_object(result.text) if result.status == "completed" else None
    if replayed or data is None or not isinstance(data.get("accounts"), list):
        data = await _read_evidence(deps, system, reader.seen_text)
    raw = data.get("accounts") if data and isinstance(data.get("accounts"), list) else []
    checked = acc.checked_accounts(raw, reader.seen_text)
    log.info("accounts kept: %d of %d (replayed %d calls)", len(checked), len(raw), replayed)
    return MatchOutcome(checked, replayed)


async def _replay_domains(deps: TurnDeps, reader: Reader, domains: Sequence[str]) -> int:
    found = replay.template_for(reader.records, domains)
    if found is None:
        return 0
    template, value = found
    missing = [d for d in domains if not replay.covered(reader.records, d)]
    return await _run(deps, reader, template.name, [replay.substitute(template.arguments, value, d) for d in missing])


async def _replay_ids(deps: TurnDeps, reader: Reader, domains: Sequence[str]) -> int:
    """Deal lookups: the model's call for one company id, repeated for the other companies' ids."""
    ids = {d: _company_ids(reader, d) for d in domains}
    known = [i for group in ids.values() for i in group]
    found = replay.template_for(reader.records, known)
    if found is None:
        return 0
    template, value = found
    missing = [i for i in dict.fromkeys(known) if not replay.covered(reader.records, i)]
    return await _run(deps, reader, template.name, [replay.substitute(template.arguments, value, i) for i in missing])


def _company_ids(reader: Reader, domain: str) -> tuple[str, ...]:
    record = replay.result_for(reader.records, domain)
    return replay.ids_near(record.text, domain) if record else ()


async def _run(deps: TurnDeps, reader: Reader, name: str, argument_sets: Sequence[dict]) -> int:
    if not argument_sets or not reader.health.is_offered(name):
        return 0
    gate = asyncio.Semaphore(REPLAY_CONCURRENCY)

    async def one(arguments: dict) -> None:
        async with gate:
            await reader.call(deps, name, arguments)

    await asyncio.gather(*(one(a) for a in argument_sets))
    return len(argument_sets)


async def _read_evidence(deps: TurnDeps, system: str, seen_text: str) -> dict | None:
    if not seen_text:
        return None
    messages = ({"role": "system", "content": system},
                {"role": "user", "content": f"Tool results:\n{prompts.fence(seen_text[:EVIDENCE_CHARS])}\n\n"
                                            f"{prompts.ACCOUNTS_FROM_EVIDENCE}"})
    return load_json_object(await ask_once(deps, messages))
