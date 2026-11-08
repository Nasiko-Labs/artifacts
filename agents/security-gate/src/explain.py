"""The model's one job on a report: explain each finding and suggest a fix, in batches of at most 8
findings, at most 5 calls, run concurrently. Validated in code: an explanation must name a finding of its
batch with the same file and line (else discarded); why is cut to 2 sentences and the fix to 3 lines;
anything claiming the code is secure or safe is discarded. The model never returns a finding list, so it
cannot add, drop or re-rate one. Secret values never reach it (the context is masked by code)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping, Sequence

import prompts
import secret_scan
from deps import TurnDeps
from reader import ask_once
from records import Finding

log = logging.getLogger("security_gate.explain")

BATCH_SIZE = 8
MAX_CALLS = 5
EXPLAIN_SECS = 90.0
MAX_WHY_SENTENCES = 2
MAX_FIX_LINES = 3
MAX_FIX_LINE_CHARS = 160
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_SAFE_CLAIM = re.compile(r"\b(?:is|are|now|looks|be)\s+(?:completely\s+|fully\s+|now\s+)?(?:secure|safe)\b",
                         re.IGNORECASE)


def load_object(text: str) -> dict | None:
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.strip("`").removeprefix("json").strip()
    for candidate in (body, body[body.find("{"): body.rfind("}") + 1] if "{" in body else ""):
        try:
            data = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def batches(findings: Sequence[Finding]) -> list[tuple[Finding, ...]]:
    chunks = [tuple(findings[i:i + BATCH_SIZE]) for i in range(0, len(findings), BATCH_SIZE)]
    return chunks[:MAX_CALLS]


async def explain(deps: TurnDeps, findings: Sequence[Finding], context: Mapping[str, str]) -> tuple[tuple[Finding, ...], int]:
    """(findings with why/fix where the model gave a valid one, how many were left unexplained)."""
    todo = [f for f in findings if not f.ignored]
    jobs = [_one(deps, batch, context) for batch in batches(todo)]
    try:
        results = await asyncio.wait_for(asyncio.gather(*jobs), timeout=EXPLAIN_SECS)
    except TimeoutError:
        log.warning("explanations ran out of time")
        results = []
    merged: dict[str, tuple[str, str]] = {k: v for r in results for k, v in r.items()}
    out = tuple(f.with_(why=merged[f.id][0], fix=merged[f.id][1]) if f.id in merged else f for f in findings)
    return out, sum(1 for f in todo if f.id not in merged)


async def _one(deps: TurnDeps, batch: Sequence[Finding], context: Mapping[str, str]) -> dict[str, tuple[str, str]]:
    blocks = []
    for f in batch:
        blocks.append(f"id: {f.id}\nfile: {f.file}\nline: {f.start_line}\nrule: {f.rule_id}\nseverity: {f.severity}\n"
                      f"cwe: {f.cwe or 'none'}\nmessage: {secret_scan.mask_model(f.message)}\ncode:\n{context.get(f.id) or '(not available)'}")
    messages = [{"role": "system", "content": prompts.EXPLAIN_SYSTEM},
                {"role": "user", "content": prompts.fence_data("\n\n".join(blocks))}]
    try:
        data = load_object(await ask_once(deps, messages)) or {}
    except Exception as e:  # noqa: BLE001 - one failed batch leaves its findings unexplained, never the report
        log.warning("explain call failed: %s", type(e).__name__)
        return {}
    return validated(data, batch)


def validated(data: Mapping, batch: Sequence[Finding]) -> dict[str, tuple[str, str]]:
    by_id = {f.id: f for f in batch}
    out: dict[str, tuple[str, str]] = {}
    for item in data.get("explanations") or ():
        if not isinstance(item, Mapping):
            continue
        f = by_id.get(str(item.get("id") or ""))
        if f is None or str(item.get("file") or "") != f.file or _int(item.get("line")) != f.start_line:
            continue
        why, fix = _why(str(item.get("why_it_matters") or "")), _fix(str(item.get("suggested_fix") or ""))
        if _SAFE_CLAIM.search(why) or _SAFE_CLAIM.search(fix):
            continue
        if why or fix:
            out[f.id] = (why, fix)
    return out


def _int(value: object) -> int:
    try:
        return int(str(value))
    except ValueError:
        return -1


def _why(text: str) -> str:
    sentences = [s for s in _SENTENCE.split(text.strip()) if s]
    return " ".join(sentences[:MAX_WHY_SENTENCES])


def _fix(text: str) -> str:
    body = text.strip().strip("`").removeprefix("python\n").strip()
    lines = [line.rstrip()[:MAX_FIX_LINE_CHARS] for line in body.splitlines() if line.strip()]
    return "\n".join(lines[:MAX_FIX_LINES])
