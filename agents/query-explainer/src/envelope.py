"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + `plain_english`, `assumptions`,
`smells`, `dialect`, `statements`). `confidence` and `needs_human` are computed here, never taken from the model.
Placeholders (`__param_n__`, `__jinja_n__`) are restored in every string."""

from __future__ import annotations

from analyze import Analysis, Statement
from explain import Explained, FallbackResult
from findings import SEVERITY_ORDER, Finding
from placeholders import Masked, unmask_deep

INDEX_RULES = ("S05", "S06", "S11")


def _smell(f: Finding, statement: int) -> dict:
    return {"kind": "smell", "id": f.rule, "ref": f.cid, "statement": statement, "severity": f.severity,
            "text": f.text, "evidence": f.evidence, "suggestion": f.suggestion, "so_what": f.so_what, "hint": f.hint}


def _assumption(f: Finding, statement: int) -> dict:
    return {"kind": "assumption", "id": f.rule, "ref": f.cid, "statement": statement, "text": f.text,
            "evidence": f.evidence, "source": f.source, "so_what": f.so_what}


def describe(s: Statement) -> str:
    """A code-only one-line summary of a statement, from its parse summary."""
    summary, query = s.summary, s.summary.get("query") or {}
    if "branches" in query:
        query = query["branches"][0]
    parts = [s.kind.upper()]
    write = summary.get("write")
    if write:
        parts.append(f"on `{write['target']}`" + ("" if write.get("where") else " (no WHERE: all rows)"))
    if query.get("from"):
        parts.append(f"reading `{query['from']}`")
    if query.get("joins"):
        parts.append("joined to " + ", ".join(f"`{j['source']}` ({j['type']})" for j in query["joins"]))
    for key, label in (("where", "filter"), ("group_by", "grouping key"), ("order_by", "ordering column")):
        if query.get(key):
            n = len(query[key])
            parts.append(f"{n} {label}{'' if n == 1 else 's'}")
    if query.get("limit"):
        parts.append(f"limit {query['limit']}")
    return ", ".join(parts) + "."


def confidence(analysis: Analysis, results: list[Explained], masked: Masked) -> str:
    if any(r.code_only == "over 4 statements" for r in results) or not any(r.lines for r in results):
        return "low"
    rules = {f.rule for r in results for f in r.findings}
    if ((not analysis.hinted and masked.has_placeholders) or any(r.repaired or not r.narrative_ok for r in results
                                                                  if not r.code_only)
            or (not analysis.schema.known and rules & set(INDEX_RULES))):
        return "medium"
    return "high"


def build(analysis: Analysis, results: list[Explained], masked: Masked, notes: tuple[str, ...] = ()) -> dict:
    smells, open_, confirmed, narrative = [], [], [], []
    for s, r in zip(analysis.statements, results):
        smells += [_smell(f, s.index) for f in r.findings if f.kind == "smell"]
        open_ += [_assumption(f, s.index) for f in (*r.findings, *r.extras) if f.kind == "assumption" and not f.checked]
        confirmed += [{"id": f.rule, "statement": s.index, "text": f.text, "note": f.note, "evidence": f.evidence}
                      for f in r.findings if f.kind == "assumption" and f.checked]
        body = "\n".join(r.lines) if r.lines else describe(s)
        narrative.append(body if len(results) == 1 else f"Statement {s.index} ({s.kind}):\n{body}")
    smells.sort(key=lambda d: SEVERITY_ORDER.get(d["severity"], 9))
    level = confidence(analysis, results, masked)
    severe = sum(1 for d in smells if d["severity"] in ("critical", "high"))
    needs = severe > 0 or level == "low"
    first = describe(analysis.statements[0]) if analysis.statements else "No query statement."
    count = len(analysis.statements)
    summary = (f"{first[:-1]}{f' (+{count - 1} more statements)' if count > 1 else ''}: {len(smells)} smells "
               f"({severe} critical/high), {len(open_)} assumptions.")
    envelope = {"summary": summary, "items": smells + open_, "plain_english": "\n\n".join(narrative),
                "assumptions": open_, "smells": smells, "confirmed_by_schema": confirmed,
                "dialect": analysis.dialect or "generic", "dialect_label": analysis.label, "statements": count,
                "skipped_statements": analysis.skipped, "dropped_model_items": sum(r.dropped for r in results),
                "coverage": list(notes), "schema_supplied": analysis.schema.known, "confidence": level,
                "needs_human": needs}
    return unmask_deep(envelope, masked.originals)


def build_fallback(result: FallbackResult, tried: str, masked: Masked, notes: tuple[str, ...] = ()) -> dict:
    smells = [_smell(f, 1) for f in sorted(result.smells, key=lambda f: SEVERITY_ORDER.get(f.severity, 9))]
    assumptions = [_assumption(f, 1) for f in result.assumptions]
    envelope = {"summary": f"Unparsed SQL (tried {tried}): {len(smells)} smells, {len(assumptions)} assumptions, "
                           "no structural checks.",
                "items": smells + assumptions, "plain_english": "\n".join(result.lines), "assumptions": assumptions,
                "smells": smells, "confirmed_by_schema": [], "dialect": "unknown", "dialect_label": "unparsed",
                "statements": 0, "skipped_statements": 0, "dropped_model_items": result.dropped,
                "coverage": list(notes), "schema_supplied": False, "confidence": "low", "needs_human": True}
    return unmask_deep(envelope, masked.originals)
