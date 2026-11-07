"""One write run: batches of <= 4 callables (<= 3 batches, concurrent model calls) -> code checks in batch
order -> at most one repair call per batch (two per turn) -> import checks. Nothing is executed."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import checks
import grounding
import imports as imports_mod
import prompts
from census import Census
from checks import Context, GenTest, Ledger
from code_model import Callable
from llm import LlmError, TurnDeps, call, complete_json, load

log = logging.getLogger("test-writer.write")

BATCH_SIZE = 4
MAX_BATCHES = 3
WHOLE_SOURCE_MAX_CHARS = 12_000


@dataclass
class Outcome:
    status: str = "ok"                             # ok | off_topic | failed
    tests: list[GenTest] = field(default_factory=list)
    helpers: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    dropped: list[tuple[str, str]] = field(default_factory=list)
    claims_removed: int = 0
    relabelled: int = 0
    repaired: bool = False
    repair_failed: bool = False
    suspected_bug: dict | None = None
    bugs_dropped: int = 0
    batches_failed: int = 0


def validate(data: Any) -> dict | None:
    if not isinstance(data, dict):
        return None
    if data.get("status") == prompts.OFF_TOPIC:
        return {"status": prompts.OFF_TOPIC, "tests": []}
    return data if isinstance(data.get("tests"), list) else None


def payload(census: Census, batch: Sequence[Callable], framework: str) -> dict:
    others = [c.signature() for c in census.callables if c not in batch][:20]
    return {"language": census.lang, "framework": framework, "callables": [{
        "name": c.name, "signature": c.signature(), "line": c.line, "raises": list(c.raises),
        "branches": c.branches, "externals_to_mock": list(c.externals),
        "edge_cases": [{"id": e.id, "case": e.label, "literal_must_appear": bool(e.probe)}
                       for e in census.cases_of(c.name)],
    } for c in batch], "other_signatures_for_context_only": others}


def source_for(census: Census, batch: Sequence[Callable], full_source: str) -> str:
    if len(full_source) <= WHOLE_SOURCE_MAX_CHARS:
        return full_source
    return "\n\n".join(c.source for c in batch)


async def write_tests(deps: TurnDeps, census: Census, framework: str, full_source: str, request: str = "",
                      style: str = "", policy: Sequence[str] = (), ctx_numbers: frozenset[float] = frozenset(),
                      existing: frozenset[str] = frozenset(), seen: set[str] | None = None,
                      module: str = "") -> Outcome:
    batches = [census.selected[i:i + BATCH_SIZE] for i in range(0, len(census.selected), BATCH_SIZE)][:MAX_BATCHES]

    async def one(batch):
        messages = prompts.write_messages(payload(census, batch, framework), source_for(census, batch, full_source),
                                          request, style, framework, policy)
        try:
            return await complete_json(deps, messages, prompts.WRITE_CONTRACT, validate)
        except LlmError as e:
            log.warning("write batch failed: %s", e)
            return None

    answers = await asyncio.gather(*(one(b) for b in batches))
    outcome, seen = Outcome(), seen if seen is not None else set()
    answered = [a for a in answers if a is not None]
    outcome.batches_failed = len(answers) - len(answered)
    if not answered:
        outcome.status = "failed"
        return outcome
    if all(a["status"] == prompts.OFF_TOPIC for a in answered):
        outcome.status = prompts.OFF_TOPIC
        return outcome
    for batch, answer in zip(batches, answers, strict=True):
        if answer is not None and answer["status"] != prompts.OFF_TOPIC:
            ctx = Context(census, framework, tuple(c.name for c in batch), ctx_numbers, existing)
            await absorb(deps, answer, ctx, seen, outcome)
    finish_imports(outcome, census, module)
    return outcome


async def absorb(deps: TurnDeps, answer: dict, ctx: Context, seen: set[str], outcome: Outcome,
                 repairable: bool = True) -> None:
    """Check one answer into `outcome`, with one repair call for its repairable tests (when allowed)."""
    ledger = Ledger()
    checks.check_tests(answer.get("tests") or (), ctx, seen, ledger, repairable)
    if ledger.repair:
        await _repair(deps, ledger, ctx, seen, outcome)
    outcome.tests += ledger.kept
    outcome.dropped += ledger.dropped
    outcome.claims_removed += ledger.claims_removed
    outcome.relabelled += ledger.relabelled
    outcome.helpers += [str(h) for h in answer.get("helpers") or () if isinstance(h, str) and h.strip()]
    outcome.imports += [str(i) for i in answer.get("imports") or () if isinstance(i, str) and i.strip()]
    bug = answer.get("suspected_bug")
    if bug and outcome.suspected_bug is None:
        outcome.suspected_bug = grounding.verified_bug(bug, ctx.census)
        outcome.bugs_dropped += outcome.suspected_bug is None


async def _repair(deps: TurnDeps, ledger: Ledger, ctx: Context, seen: set[str], outcome: Outcome) -> None:
    broken = [raw for raw, _ in ledger.repair]
    names = {str(raw.get("name")): found for raw, found in ledger.repair}
    ledger.repair = []
    if not deps.has_time() or not deps.budget.take_repair():
        ledger.dropped += [(n, "; ".join(f)) for n, f in names.items()]
        outcome.repair_failed = True
        return
    outcome.repaired = True
    try:
        data = validate(load(await call(deps, prompts.repair_messages(broken, names, ctx.framework))))
    except LlmError as e:
        log.warning("repair failed: %s", e)
        data = None
    before, dropped_before = len(ledger.kept), len(ledger.dropped)
    checks.check_tests((data or {}).get("tests") or (), ctx, seen, ledger, repairable=False)
    rechecked = [(n, f"could not be repaired: {r}") for n, r in ledger.dropped[dropped_before:]]
    ledger.dropped[dropped_before:] = rechecked
    answered = {t.name for t in ledger.kept[before:]} | {n for n, _ in rechecked}
    ledger.dropped += [(n, "could not be repaired: " + "; ".join(f)) for n, f in names.items() if n not in answered]
    if len(ledger.kept) - before < len(broken):
        outcome.repair_failed = True


def finish_imports(outcome: Outcome, census: Census, module: str = "") -> None:
    """Judge every import; a test that relies on a refused import is dropped with it."""
    family = census.family
    modules = {m for m in [*census.modules.values(), module] if m} | {"module_under_test", "./module_under_test"}
    names, source_imports = census.names(), set(census.source_imports)
    refused: list[str] = []
    kept_imports = []
    for line in dict.fromkeys(outcome.imports):
        verdict = imports_mod.judge(line, family, modules, names, source_imports)
        if verdict.ok:
            kept_imports.append(line)
        else:
            refused += list(verdict.bound)
            outcome.dropped.append((line, verdict.reason))
    tests = []
    for test in outcome.tests:
        bad = [imports_mod.judge(i, family, modules, names, source_imports) for i in test.imports]
        reason = next((v.reason for v in bad if not v.ok), "")
        uses = next((b for b in refused if b and re.search(rf"\b{re.escape(b)}\b", test.code)), "")
        if reason or uses:
            outcome.dropped.append((test.name, reason or f"uses the refused import {uses}"))
        else:
            tests.append(test)
    outcome.tests, outcome.imports = tests, kept_imports
