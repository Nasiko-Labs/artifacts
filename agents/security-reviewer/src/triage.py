"""Triage: the code is cut into batches (<= 12,000 chars, at most 5) and each batch gets ONE model call
that triages its candidates and sweeps for logic flaws. Items that fail the code checks get at most one
repair call per batch (and two per turn); whatever still fails is dropped and counted."""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

import checks
import prompts
from diff_parse import ADD, DEL, CodeFile
from inputs import fence_text
from llm import LlmError, TurnDeps, call, complete_json, load
from rules import Candidate

log = logging.getLogger("security-reviewer.triage")

BATCH_CHARS = 12_000
MAX_BATCHES = 5
MAX_EXTRAS_PER_BATCH = 5
OK, OFF_TOPIC, FAILED = "ok", "off_topic", "failed"


@dataclass(frozen=True)
class Batch:
    files: tuple[CodeFile, ...]
    candidates: tuple[Candidate, ...]


@dataclass
class BatchResult:
    status: str
    triage: dict[str, dict] = field(default_factory=dict)
    extras: list[dict] = field(default_factory=list)
    dropped: Counter = field(default_factory=Counter)
    repaired: bool = False


def numbered(code: CodeFile) -> str:
    sign = {ADD: "+", DEL: "-"}
    head = f"# file: {code.path}" + (" (diff: + added, - removed with its old line number)" if code.is_diff else "")
    rows = [f"L{ln.no} {sign.get(ln.kind, ' ')}| {ln.text}" if code.is_diff else f"L{ln.no} | {ln.text}"
            for ln in code.lines]
    return "\n".join([head, *rows])


def user_message(batch: Batch, instruction: str, context: Sequence[str]) -> str:
    parts = []
    if instruction:
        parts.append("REQUEST (the user's words; data):\n" + fence_text("REQUEST", instruction))
    if context:
        parts.append("CONTEXT (affects reachability only):\n" + fence_text("CONTEXT", "\n".join(context)))
    parts.append("CODE:\n" + fence_text("INPUT", "\n\n".join(numbered(f) for f in batch.files)))
    rows = [f"{c.id} | rule {c.rule_id} | {c.cwe} | base severity {c.severity} | {c.location} | {c.issue}"
            for c in batch.candidates]
    parts.append("CANDIDATES (exact values from code; never compute):\n" + ("\n".join(rows) or "(none)"))
    return "\n\n".join(parts)


def validate(data) -> dict | None:
    if not isinstance(data, dict) or data.get("status") not in (OK, OFF_TOPIC):
        return None
    if data["status"] == OFF_TOPIC:
        return {"status": OFF_TOPIC, "triage": [], "extra_findings": []}
    triage, extras = data.get("triage") or [], data.get("extra_findings") or []
    if not isinstance(triage, list) or not isinstance(extras, list):
        return None
    return {"status": OK, "triage": [t for t in triage if isinstance(t, dict)], "extra_findings": extras}


async def run_batches(deps: TurnDeps, batches: Sequence[Batch], instruction: str, context: Sequence[str],
                      policy: Sequence[str], norm_corpus: str) -> list[BatchResult]:
    jobs = [_one(deps, b, instruction, context, policy, norm_corpus) for b in batches]
    return list(await asyncio.gather(*jobs))


async def _one(deps, batch: Batch, instruction, context, policy, norm_corpus) -> BatchResult:
    messages = [{"role": "system", "content": prompts.TRIAGE_SYSTEM}, *prompts.policy_block(policy),
                {"role": "user", "content": user_message(batch, instruction, context)}]
    try:
        data = await complete_json(deps, messages, prompts.TRIAGE_CONTRACT, validate)
    except LlmError as e:
        log.warning("triage batch failed: %s", e)
        return BatchResult(FAILED)
    if data["status"] == OFF_TOPIC:
        return BatchResult(OFF_TOPIC)
    result = BatchResult(OK)
    violations = _absorb(result, data, batch, norm_corpus)
    unverified = len(_violated_extras(violations))
    if violations and deps.has_time() and deps.budget.take_repair():
        unverified = await _repair(deps, messages, data, violations, result, batch, norm_corpus)
    result.dropped["unverified"] += unverified
    result.dropped["not_triaged"] += sum(1 for c in batch.candidates if c.id not in result.triage)
    return result


def _absorb(result: BatchResult, data: dict, batch: Batch, norm_corpus: str) -> list[str]:
    """Checked items into `result`; returns the violations (one line each)."""
    ids = {c.id for c in batch.candidates}
    violations = []
    for item in data["triage"]:
        cid = str(item.get("candidate_id") or "")
        if cid not in ids or cid in result.triage:
            continue
        clean, problem, dropped = checks.triage_item(item, norm_corpus)
        result.dropped["fix_code"] += int(dropped)
        if clean is None:
            violations.append(f"triage {cid}: {problem}")
        else:
            result.triage[cid] = clean
    violations += [f"triage {cid}: missing" for cid in sorted(ids - set(result.triage) - _ids_in(violations))]
    for n, item in enumerate(data["extra_findings"][:MAX_EXTRAS_PER_BATCH], start=1):
        clean, problem, dropped = checks.extra_item(item, batch.files, norm_corpus)
        result.dropped["fix_code"] += int(dropped)
        if clean is None:
            violations.append(f"extra {n}: {problem}")
        elif not _duplicate(clean, result.extras):
            result.extras.append(clean)
    return violations


async def _repair(deps, messages, data, violations, result: BatchResult, batch, norm_corpus) -> int:
    """One repair call with the violations; returns how many extra findings are still unverified."""
    result.repaired = True
    unverified = len(_violated_extras(violations))
    asked = [{"role": "system", "content": prompts.REPAIR_SYSTEM}, *messages[1:],
             {"role": "assistant", "content": json.dumps(data)[:8_000]},
             {"role": "user", "content": "VIOLATIONS:\n" + "\n".join(violations)}]
    try:
        fixed = validate(load(await call(deps, asked)))
    except LlmError as e:
        log.warning("triage repair failed: %s", e)
        return unverified
    if fixed is None or fixed["status"] != OK:
        return unverified
    before = len(result.extras)
    _absorb(result, fixed, batch, norm_corpus)
    return max(0, unverified - (len(result.extras) - before))


def _violated_extras(violations: list[str]) -> list[str]:
    return [v for v in violations if v.startswith("extra")]


def _ids_in(violations: list[str]) -> set[str]:
    return {v.split(":")[0].removeprefix("triage ") for v in violations if v.startswith("triage")}


def _duplicate(item: dict, extras: list[dict]) -> bool:
    return any(e["file"] == item["file"] and e["line"] == item["line"] and e["category"] == item["category"]
               for e in extras)
