"""The model steps: one explain call per statement (at most 4, run concurrently), at most one repair call per
turn for a narrative that misses a CTE/table or names something not in the SQL, and the fallback reading when
nothing parsed. Every model string passes the checks in checks.py before it is kept."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import checks
import prompts
from analyze import Analysis, Statement
from findings import Finding
from llm import LlmError, TurnDeps, call, complete_json, load

MAX_EXPLAINED = 4
MAX_PRETTY_CHARS = 12_000
SEVERITIES = ("critical", "high", "medium", "low")
MAX_FALLBACK_ITEMS = 5


@dataclass(frozen=True)
class Explained:
    index: int
    lines: tuple[str, ...] = ()
    findings: tuple[Finding, ...] = ()
    extras: tuple[Finding, ...] = ()
    dropped: int = 0
    narrative_ok: bool = True
    repaired: bool = False
    code_only: str = ""           # why no narrative: "" | "over 4 statements" | "too long" | "model failed"
    off_topic: bool = False
    bad_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class FallbackResult:
    status: str
    lines: tuple[str, ...] = ()
    assumptions: tuple[Finding, ...] = ()
    smells: tuple[Finding, ...] = ()
    dropped: int = 0
    notes: tuple[str, ...] = field(default_factory=tuple)


def _validate_explain(data) -> dict | None:
    if not isinstance(data, dict) or data.get("status") not in ("ok", "off_topic"):
        return None
    if data["status"] == "ok" and not str(data.get("plain_english") or "").strip():
        return None
    return data


async def explain_all(deps: TurnDeps, analysis: Analysis, schema_text: str, request: str,
                      policy: tuple[str, ...]) -> list[Explained]:
    targets = [s for s in analysis.statements[:MAX_EXPLAINED] if len(s.pretty) <= MAX_PRETTY_CHARS]
    calls = [_explain_one(deps, analysis, s, schema_text, request, policy) for s in targets]
    done = {e.index: e for e in await asyncio.gather(*calls)}
    out = []
    for s in analysis.statements:
        if s.index in done:
            out.append(done[s.index])
        else:
            why = "too long to explain in one call" if s.index <= MAX_EXPLAINED else "over 4 statements"
            out.append(Explained(s.index, findings=s.findings, code_only=why))
    return await _repair_first(deps, analysis, out)


async def _explain_one(deps: TurnDeps, analysis: Analysis, s: Statement, schema_text: str, request: str,
                       policy: tuple[str, ...]) -> Explained:
    listed = [{"id": f.cid, "kind": f.kind, "severity": f.severity, "finding": f.text, "evidence": f.evidence}
              for f in s.findings if not f.checked]
    messages = prompts.explain_messages(s.pretty, s.summary, listed, schema_text, analysis.label, request, policy)
    try:
        data = await complete_json(deps, messages, prompts.EXPLAIN_CONTRACT, _validate_explain)
    except LlmError:
        return Explained(s.index, findings=s.findings, code_only="model failed")
    if data["status"] == "off_topic":
        return Explained(s.index, findings=s.findings, off_topic=True)
    return _checked(analysis, s, data)


def _checked(analysis: Analysis, s: Statement, data: dict, repaired: bool = False) -> Explained:
    raw_lines = checks.steps(str(data.get("plain_english")))
    bad = tuple(n for line in raw_lines for n in checks.unknown_names(line, analysis.names, analysis.corpus))
    lines, dropped = checks.drop_unknown(raw_lines, analysis.names, analysis.corpus)
    findings, note_drops = checks.merge_notes(list(s.findings), data.get("notes"), analysis.names, analysis.corpus)
    extras, extra_drops = checks.extra_assumptions(data.get("extra_assumptions"), analysis.names, analysis.corpus)
    ok = not dropped and not checks.missing_mentions("\n".join(lines), s.must_mention)
    return Explained(s.index, tuple(lines), tuple(findings), tuple(extras), dropped + note_drops + extra_drops,
                     narrative_ok=ok, repaired=repaired, bad_names=bad)


async def _repair_first(deps: TurnDeps, analysis: Analysis, results: list[Explained]) -> list[Explained]:
    """One repair call for the first statement whose narrative failed the set or name check."""
    for pos, result in enumerate(results):
        if result.narrative_ok or result.code_only or result.off_topic:
            continue
        s = analysis.statements[pos]
        missing = checks.missing_mentions("\n".join(result.lines), s.must_mention)
        if not deps.has_time() or not deps.budget.take_repair():
            return results
        try:
            raw = await call(deps, prompts.repair_messages("\n".join(result.lines) or "(empty)", missing,
                                                           list(result.bad_names), s.pretty))
        except LlmError:
            return results
        data = _validate_explain(load(raw))
        if data is None or data["status"] != "ok":
            return results
        fixed = _checked(analysis, s, {**data, "notes": {}, "extra_assumptions": []}, repaired=True)
        merged = Explained(result.index, fixed.lines, result.findings, result.extras,
                           result.dropped + fixed.dropped, fixed.narrative_ok, True)
        return [*results[:pos], merged, *results[pos + 1:]]
    return results


async def fallback(deps: TurnDeps, raw_sql: str, schema_text: str, request: str,
                   policy: tuple[str, ...]) -> FallbackResult:
    def valid(data):
        ok = isinstance(data, dict) and data.get("status") in ("ok", "off_topic")
        return data if ok else None

    try:
        data = await complete_json(deps, prompts.fallback_messages(raw_sql, schema_text, request, policy),
                                   prompts.FALLBACK_CONTRACT, valid)
    except LlmError:
        return FallbackResult("failed")
    if data["status"] == "off_topic":
        return FallbackResult("off_topic")
    lines = tuple(checks.steps(str(data.get("plain_english") or "")))
    found, dropped = [], 0
    for key, kind in (("assumptions", "assumption"), ("smells", "smell")):
        for n, item in enumerate((data.get(key) or [])[:MAX_FALLBACK_ITEMS] if isinstance(data.get(key), list)
                                 else [], start=1):
            text = " ".join(str((item or {}).get("text") or "").split())[:checks.MAX_SENTENCE_CHARS]
            evidence = " ".join(str((item or {}).get("evidence") or "").split())
            if not text or not checks.evidence_ok(evidence, raw_sql):
                dropped += 1
                continue
            severity = str(item.get("severity") or "low").casefold() if kind == "smell" else ""
            found.append(Finding("M", kind, text, evidence, severity if kind != "smell" or severity in SEVERITIES
                                 else "low", source="model", cid=f"M{kind[0].upper()}-{n}"))
    return FallbackResult("ok", lines, tuple(f for f in found if f.kind == "assumption"),
                          tuple(f for f in found if f.kind == "smell"), dropped)
