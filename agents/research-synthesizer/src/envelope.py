"""Pure: the output envelope (`summary`, `items`, `confidence`, `needs_human` + this agent's fields) and the
scope statement, all computed in code from the stored brief."""

from __future__ import annotations

from collections import Counter

from model import Finding, Source


def scope(sources: list[Source]) -> str:
    read = [s for s in sources if s.read != "not_read"]
    names = ", ".join(f"{s.id} {s.label}" for s in read) or "none"
    words = sum(s.words for s in read)
    dated = sorted(s.date for s in read if s.date)
    undated = len(read) - len(dated)
    if dated:
        dates = f"dates range {dated[0]} to {dated[-1]} (as stated; {undated} undated)"
    else:
        dates = "no source states a date"
    types = Counter(s.type for s in read if s.type)
    kinds = ", ".join(f"{n} {t}" for t, n in sorted(types.items())) if types else "not stated"
    unit = "source" if len(read) == 1 else "sources"
    return f"Read {len(read)} {unit} ({names}), {words:,} words; {dates}; types: {kinds}."


def summary(brief: dict) -> str:
    findings = [Finding.from_dict(f) for f in brief["findings"]]
    sources = brief["sources"]
    levels = Counter(f.confidence for f in findings)
    conflicts = sum(f.agreement == "conflict" for f in findings)
    unknowns = len(brief["unknowns"])
    return (f"{len(sources)} {'source' if len(sources) == 1 else 'sources'}, {len(findings)} findings "
            f"({levels['high']} high, {levels['medium']} medium, {levels['low']} low), {conflicts} "
            f"{'conflict' if conflicts == 1 else 'conflicts'}, {unknowns} {'unknown' if unknowns == 1 else 'unknowns'}")


def items(brief: dict) -> list[dict]:
    claims = {c["id"]: c for c in brief["claims"]}
    out = []
    for raw in brief["findings"]:
        f = Finding.from_dict(raw)
        quotes = [{"source_id": claims[i]["source_id"], "quote": claims[i]["quote"], "kind": claims[i]["kind"]}
                  for i in f.claim_ids if i in claims]
        conflict = None
        if f.conflict:
            pair = [claims[i] for i in (f.conflict["a"], f.conflict["b"]) if i in claims]
            conflict = {"quotes": [{"source_id": c["source_id"], "quote": c["quote"]} for c in pair],
                        "difference": f.conflict["difference"]}
        out.append({"id": f.id, "statement": f.statement, "source_ids": list(f.source_ids), "quotes": quotes,
                    "agreement": f.agreement, "conflict": conflict, "confidence": f.confidence,
                    "is_view": f.is_view})
    return out


def build(brief: dict, markdown: str) -> dict:
    sources = [Source.from_dict(s) for s in brief["sources"]]
    return {"summary": summary(brief), "items": items(brief), "brief_markdown": markdown,
            "sources": [s.to_dict(with_text=False) for s in sources], "unknowns": list(brief["unknowns"]),
            "scope": scope(sources), "question": brief["question"], "bottom_line": brief["bottom_line"],
            "confidence": brief["confidence"], "needs_human": brief["needs_human"], "dropped": dict(brief["drops"])}
